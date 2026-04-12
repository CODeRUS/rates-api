#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот (Telethon): /rates, /usdt, /cash, /exchange, /calc.

Переменные можно задать в файле ``.env`` в корне репозитория (подхватывается при старте).

Запуск из корня репозитория::

    export TELEGRAM_API_ID="611335"
    export TELEGRAM_API_HASH="…"
    export TELEGRAM_BOT_TOKEN="…"
    export BOT_ADMIN_ID="123456789"  # опционально: Telegram user id — только он может /refresh
    python -m bot.main

Секреты не коммитьте. Файл сессии: ``bot/rates_bot.session`` (в .gitignore).
Опционально ``BOT_ADMIN_ID``: ``/refresh`` — сброс кеша сводки; ``/refresh usdt`` — сброс кеша отчёта USDT.
В личке с ботом текст GPT-пользователя **без** ``/``: при ``CHAT_AGENT_URL`` и ``CHAT_AGENT_SHARED_SECRET`` — запрос в сервис ``chat_agent`` (POST ``/chat``); иначе прямой OpenAI Chat (``OPENAI_API_KEY``, ``OPENAI_API_URL``, см. ``openai_gpt``). ``OPENAI_PROMPT`` подмешивается в агент только если ``include_env_system`` (не админ).
Опционально ``BOT_FETCH_TIMEOUT_SEC`` (по умолчанию 180): таймаут сборки сводки/USDT/cash/exchange в потоке.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Set

_ROOT = Path(__file__).resolve().parent.parent

# Таймаут сбора сводки/USDT в фоновом потоке (сек). Иначе один зависший HTTP оставляет чат «занятым» навсегда.
_DEFAULT_FETCH_TIMEOUT_SEC = 180.0
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_loader import load_repo_dotenv

load_repo_dotenv(_ROOT)

from telethon import TelegramClient, events

from bot.rates_tokens import parse_rates_command_tokens
from bot.calc_args import parse_calc_command_args
from bot.rshb_args import parse_rshb_command_args
import cash_report as _cash_report

from bot.summary_adapter import (
    get_calc_text,
    get_cash_cities_text,
    get_cash_text,
    get_exchange_text,
    get_rshb_text,
    get_summary_text,
    get_usdt_text,
    run_background_unified_refresh,
    split_for_telegram,
)

logger = logging.getLogger(__name__)

def _gpt_data_path(env_name: str, default: Path) -> Path:
    raw = (os.environ.get(env_name) or "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (_ROOT / p).resolve()


_GPT_USERS_FILE = _gpt_data_path("BOT_GPT_USERS_FILE", _ROOT / "gpt-users.txt")
_GPT_PENDING_FILE = _gpt_data_path("BOT_GPT_PENDING_FILE", _ROOT / "gpt-pending.txt")


def _load_id_file(path: Path) -> Set[int]:
    ids: Set[int] = set()
    if not path.is_file():
        return ids
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return ids
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        try:
            ids.add(int(s))
        except ValueError:
            continue
    return ids


def _save_id_file(path: Path, header: str, ids: Set[int]) -> bool:
    lines = [header]
    for uid in sorted(ids):
        lines.append(f"{uid}\n")
    try:
        path.write_text("".join(lines), encoding="utf-8")
        return True
    except OSError:
        return False


def _load_gpt_user_ids() -> Set[int]:
    return _load_id_file(_GPT_USERS_FILE)


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _fetch_timeout_sec() -> float:
    raw = (os.environ.get("BOT_FETCH_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_FETCH_TIMEOUT_SEC
    try:
        v = float(raw)
        return v if v > 0 else _DEFAULT_FETCH_TIMEOUT_SEC
    except ValueError:
        return _DEFAULT_FETCH_TIMEOUT_SEC


def _credentials_ok() -> bool:
    return (
        _env_int("TELEGRAM_API_ID") is not None
        and bool(_env("TELEGRAM_API_HASH"))
        and bool(_env("TELEGRAM_BOT_TOKEN"))
    )


async def _main_async() -> None:
    api_id = _env_int("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    if api_id is None or not api_hash or not bot_token:
        print(
            "Задайте TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN в окружении.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    sess_dir_raw = (os.environ.get("TELETHON_SESSION_DIR") or "").strip()
    sess_dir = Path(sess_dir_raw) if sess_dir_raw else (_ROOT / "bot")
    sess_dir.mkdir(parents=True, exist_ok=True)
    session_path = sess_dir / "rates_bot"
    client = TelegramClient(str(session_path), api_id, api_hash)

    rates_busy_guard = asyncio.Lock()
    rates_busy_chats: Set[int] = set()
    fetch_timeout = _fetch_timeout_sec()
    gpt_allowed_users: Set[int] = _load_gpt_user_ids()
    gpt_pending_users: Set[int] = _load_id_file(_GPT_PENDING_FILE)

    async def _send_rates_summary(
        event: events.NewMessage.Event,
        *,
        refresh: bool,
        output_filter: str = "",
    ) -> None:
        chat_id = event.chat_id
        # Не держим Lock во время await: иначе завершившийся запрос в finally может ждать lock
        # у второго сообщения «уже выполняется» и долго не снимает chat_id из множества.
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт получение…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_summary_text,
                        refresh=refresh,
                        output_filter=output_filter,
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "get_summary_text timed out after %.0fs (refresh=%s)",
                    fetch_timeout,
                    refresh,
                )
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при сборе сводки. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_summary_text failed (refresh=%s)", refresh)
                await status_msg.edit("Не удалось собрать сводку. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустая сводка)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
            if getattr(get_summary_text, "_needs_background_refresh", False):
                asyncio.create_task(
                    asyncio.to_thread(run_background_unified_refresh, "summary")
                )
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_cash_report(
        event: events.NewMessage.Event,
        *,
        city_n: int | None,
        top_n: int = 3,
        use_rbc: bool = True,
        use_banki: bool = True,
        use_vbr: bool = True,
    ) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            if city_n is None:
                await event.respond(get_cash_cities_text())
                return
            cities = [x[0] for x in _cash_report._CASH_LOCATIONS]
            if city_n < 1 or city_n > len(cities):
                await event.respond(f"Номер города должен быть от 1 до {len(cities)}.")
                return
            city_label = cities[city_n - 1]
            status_msg = await event.respond("Идёт получение cash…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_cash_text,
                        refresh=False,
                        top_n=top_n,
                        city_label=city_label,
                        use_rbc=use_rbc,
                        use_banki=use_banki,
                        use_vbr=use_vbr,
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("get_cash_text timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при сборе cash. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_cash_text failed")
                await status_msg.edit("Не удалось собрать cash. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустой отчёт cash)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
            if getattr(get_cash_text, "_needs_background_refresh", False):
                asyncio.create_task(
                    asyncio.to_thread(run_background_unified_refresh, "cash")
                )
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_exchange_report(
        event: events.NewMessage.Event, *, top_n: int
    ) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт получение exchange…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_exchange_text,
                        refresh=False,
                        top_n=top_n,
                        lang="ru",
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("get_exchange_text timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при сборе exchange. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_exchange_text failed")
                await status_msg.edit(
                    "Не удалось собрать exchange. Попробуйте позже."
                )
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустой отчёт exchange)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
            if getattr(get_exchange_text, "_needs_background_refresh", False):
                asyncio.create_task(
                    asyncio.to_thread(run_background_unified_refresh, "exchange")
                )
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_usdt_report(
        event: events.NewMessage.Event, *, refresh: bool
    ) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash или /exchange). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond(
                "Обновление отчёта USDT…" if refresh else "Идёт получение USDT…"
            )
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(get_usdt_text, refresh=refresh),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("get_usdt_text timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при сборе USDT. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_usdt_text failed")
                await status_msg.edit("Не удалось собрать отчёт USDT. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустой отчёт USDT)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
            if (
                not refresh
                and getattr(get_usdt_text, "_needs_background_refresh", False)
            ):
                asyncio.create_task(
                    asyncio.to_thread(run_background_unified_refresh, "usdt")
                )
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_rshb_report(
        event: events.NewMessage.Event, *, thb_nets: list[float], atm_fee: float
    ) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт получение RSHB…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_rshb_text, thb_nets=thb_nets, atm_fee=atm_fee
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("get_rshb_text timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при сборе RSHB. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_rshb_text failed")
                await status_msg.edit("Не удалось собрать отчёт RSHB. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустой отчёт RSHB)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_calc_report(
        event: events.NewMessage.Event,
        *,
        budget_rub: float,
        fiat_code: str,
        rub_per_fiat: float,
    ) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт расчёт calc…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_calc_text,
                        budget_rub=budget_rub,
                        fiat_code=fiat_code,
                        rub_per_fiat=rub_per_fiat,
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("get_calc_text timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при расчёте calc. "
                    "Проверьте сеть или задайте BOT_FETCH_TIMEOUT_SEC."
                )
                return
            except Exception:
                logger.exception("get_calc_text failed")
                await status_msg.edit("Не удалось выполнить calc. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустой отчёт calc)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _refresh_cash_cache(event: events.NewMessage.Event) -> None:
        """Админский прогрев кеша cash для всех городов."""
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt, /cash, /exchange, /rshb или /calc). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Обновление кеша cash по всем городам…")
            try:
                _ = await asyncio.wait_for(
                    asyncio.to_thread(
                        get_cash_text,
                        refresh=True,
                        top_n=20,
                        city_label="",
                    ),
                    timeout=fetch_timeout,
                )
            except asyncio.TimeoutError:
                logger.error("refresh cash timed out after %.0fs", fetch_timeout)
                await status_msg.edit(
                    f"Таймаут {fetch_timeout:.0f} с при обновлении cash-кеша."
                )
                return
            except Exception:
                logger.exception("refresh cash cache failed")
                await status_msg.edit("Не удалось обновить кеш cash.")
                return
            await status_msg.edit("Кеш cash обновлён (все города).")
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    @client.on(events.NewMessage(pattern=r"(?i)^/start(?:@\S+)?$"))
    async def on_start(event: events.NewMessage.Event) -> None:
        await event.respond(
            "Команды:\n"
            "/rates — сводка RUB/THB; /rates ta или /rates filter ta — пресет; неизвестный фильтр без ошибки\n"
            "/usdt — P2P RUB/USDT и USDT/THB\n"
            "/cash — список городов; /cash N [banki|vbr|rbc|all] [K] — курсы города, опц. источник и топ K\n"
            "/exchange — топ филиалов TT по USD/EUR/CNY→THB (опц. число: /exchange 5)\n"
            "/rshb — THB/RUB РСХБ; /rshb THB [ATM_FEE] или несколько сумм, последнее — комиссия ATM\n"
            "/calc — сравнение каналов RUB→THB; /calc RUB usd|eur|cny КУРС (₽ за 1 ед. валюты для TT)\n"
        )

    @client.on(events.NewMessage(pattern=r"(?i)^/cash(?:@\S+)?(?:\s+\S+){0,4}$"))
    async def on_cash(event: events.NewMessage.Event) -> None:
        msg = (event.message.message or "").strip()
        tokens = msg.split()
        city_n: int | None = None
        top_n = 3
        use_rbc, use_banki, use_vbr = True, True, True
        _SRC = frozenset({"all", "banki", "vbr", "rbc"})
        if len(tokens) > 1:
            try:
                city_n = int(tokens[1])
            except ValueError:
                await event.respond(
                    "После /cash укажите номер города из списка, например: /cash 1"
                )
                return
        rest = tokens[2:]
        i = 0
        source_spec: str | None = None
        if i < len(rest):
            low = rest[i].lower()
            if low in _SRC:
                source_spec = low
                i += 1
            elif rest[i].isdigit():
                top_n = int(rest[i])
                i += 1
        if i < len(rest):
            if rest[i].isdigit():
                top_n = min(int(rest[i]), 50)
            elif rest[i].lower() in _SRC and source_spec is None:
                source_spec = rest[i].lower()
        if source_spec:
            try:
                use_rbc, use_banki, use_vbr = _cash_report.parse_cash_sources_str(
                    source_spec
                )
            except ValueError as e:
                await event.respond(f"Источник: {e}")
                return
        if top_n < 1:
            await event.respond("Число строк top должно быть не меньше 1.")
            return
        top_n = min(top_n, 50)
        await _send_cash_report(
            event,
            city_n=city_n,
            top_n=top_n,
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
        )

    @client.on(events.NewMessage(pattern=r"(?i)^/exchange(?:@\S+)?"))
    async def on_exchange(event: events.NewMessage.Event) -> None:
        msg = (event.message.message or "").strip()
        tokens = msg.split()
        top_n = 10
        if len(tokens) > 1:
            try:
                top_n = int(tokens[1])
            except ValueError:
                await event.respond(
                    "После /exchange укажите число филиалов, например: /exchange 5"
                )
                return
            if top_n < 1:
                await event.respond("Число филиалов должно быть не меньше 1.")
                return
            top_n = min(top_n, 50)
        await _send_exchange_report(event, top_n=top_n)

    @client.on(events.NewMessage(pattern=r"(?i)^/usdt(?:@\S+)?$"))
    async def on_usdt(event: events.NewMessage.Event) -> None:
        await _send_usdt_report(event, refresh=False)

    @client.on(events.NewMessage(pattern=r"(?i)^/calc(?:@\S+)?(?:\s|$)"))
    async def on_calc(event: events.NewMessage.Event) -> None:
        msg = (event.message.message or "").strip()
        try:
            budget_rub, fiat_code, rub_per_fiat = parse_calc_command_args(msg)
        except ValueError as e:
            hint = str(e).strip() or "Формат: /calc RUB usd|eur|cny КУРС"
            await event.respond(hint)
            return
        await _send_calc_report(
            event,
            budget_rub=budget_rub,
            fiat_code=fiat_code,
            rub_per_fiat=rub_per_fiat,
        )

    @client.on(events.NewMessage(pattern=r"(?i)^/rshb(?:@\S+)?(?:\s+\S+)*$"))
    async def on_rshb(event: events.NewMessage.Event) -> None:
        msg = (event.message.message or "").strip()
        try:
            thb_nets, atm_fee = parse_rshb_command_args(msg)
        except ValueError:
            await event.respond(
                "Формат: /rshb [THB] [ATM_FEE] или /rshb 30000 20000 10000 250 "
                "(несколько снятий, последнее число — комиссия ATM)."
            )
            return
        await _send_rshb_report(event, thb_nets=thb_nets, atm_fee=atm_fee)

    @client.on(events.NewMessage(pattern=r"(?i)^/rates(?:@\S+)?"))
    async def on_rates(event: events.NewMessage.Event) -> None:
        msg = event.message.message or ""
        tokens = msg.split()
        refresh, output_filter = parse_rates_command_tokens(tokens)
        await _send_rates_summary(
            event, refresh=refresh, output_filter=output_filter
        )

    @client.on(events.NewMessage(pattern=r"(?i)^/refresh(?:@\S+)?"))
    async def on_refresh(event: events.NewMessage.Event) -> None:
        admin_id = _env_int("BOT_ADMIN_ID")
        if admin_id is None:
            await event.respond(
                "Команда /refresh не настроена (задайте BOT_ADMIN_ID в окружении)."
            )
            return
        sender = event.sender_id
        if sender is None or int(sender) != admin_id:
            await event.respond("Доступно только администратору.")
            return
        tokens = (event.message.message or "").split()
        sub = tokens[1].lower() if len(tokens) > 1 else None
        if sub == "usdt":
            logger.info("Admin /refresh usdt from sender_id=%s", sender)
            await _send_usdt_report(event, refresh=True)
            return
        if sub == "cash":
            logger.info("Admin /refresh cash from sender_id=%s", sender)
            await _refresh_cash_cache(event)
            return
        if len(tokens) > 1:
            await event.respond(
                "Неизвестная подкоманда. Доступно: /refresh, /refresh usdt, /refresh cash"
            )
            return
        logger.info("Admin /refresh from sender_id=%s", sender)
        await _send_rates_summary(event, refresh=True)

    admin_id_for_gpt = _env_int("BOT_ADMIN_ID")

    def _is_gpt_user(user_id: int) -> bool:
        return (admin_id_for_gpt is not None and user_id == admin_id_for_gpt) or (
            user_id in gpt_allowed_users
        )

    def _append_gpt_user(user_id: int) -> bool:
        if user_id in gpt_allowed_users:
            return False
        gpt_allowed_users.add(user_id)
        line = f"{user_id}\n"
        try:
            if not _GPT_USERS_FILE.exists():
                _GPT_USERS_FILE.write_text(
                    "# Telegram user ids with GPT access (one per line)\n",
                    encoding="utf-8",
                )
            with _GPT_USERS_FILE.open("a", encoding="utf-8") as f:
                f.write(line)
            return True
        except OSError:
            gpt_allowed_users.discard(user_id)
            return False

    def _remove_gpt_user(user_id: int) -> bool:
        if user_id not in gpt_allowed_users:
            return False
        gpt_allowed_users.discard(user_id)
        lines = [
            "# Telegram user ids with GPT access (one per line)\n",
        ]
        for uid in sorted(gpt_allowed_users):
            lines.append(f"{uid}\n")
        try:
            _GPT_USERS_FILE.write_text("".join(lines), encoding="utf-8")
            return True
        except OSError:
            gpt_allowed_users.add(user_id)
            return False

    def _append_pending_user(user_id: int) -> bool:
        if user_id in gpt_pending_users:
            return False
        gpt_pending_users.add(user_id)
        header = "# Telegram user ids with pending GPT access requests (one per line)\n"
        if not _save_id_file(_GPT_PENDING_FILE, header, gpt_pending_users):
            gpt_pending_users.discard(user_id)
            return False
        return True

    def _remove_pending_user(user_id: int) -> None:
        if user_id not in gpt_pending_users:
            return
        gpt_pending_users.discard(user_id)
        if not gpt_pending_users:
            try:
                if _GPT_PENDING_FILE.exists():
                    _GPT_PENDING_FILE.unlink()
            except OSError:
                return
            return
        header = "# Telegram user ids with pending GPT access requests (one per line)\n"
        _save_id_file(_GPT_PENDING_FILE, header, gpt_pending_users)

    def _gpt_message(e: events.NewMessage.Event) -> bool:
        if not e.is_private:
            return False
        if bool(getattr(e.message, "out", False)):
            return False
        sid = e.sender_id
        if sid is None:
            return False
        msg = (e.message.message or "").strip()
        if not msg or msg.startswith("/"):
            return False
        return _is_gpt_user(int(sid))

    @client.on(events.NewMessage(pattern=r"^/gpt($|\s)"))
    async def on_gpt_request(event: events.NewMessage.Event) -> None:
        """Скрытая команда /gpt: запрос доступа (для пользователя) или список ожиданий (для админа)."""
        sid = event.sender_id
        if sid is None:
            return
        uid = int(sid)
        if admin_id_for_gpt is not None and uid == admin_id_for_gpt:
            # Админ: показать список ожидающих подтверждения.
            if not gpt_pending_users:
                await event.respond("GPT pending: пусто (нет ожидающих подтверждения).")
                return
            lines = ["GPT pending (ожидают /gpt_add):"]
            for pid in sorted(gpt_pending_users):
                lines.append(f"  • {pid} — `/gpt_add {pid}`")
            await event.respond("\n".join(lines))
            return
        if not event.is_private:
            await event.respond("Команду /gpt отправляйте в личку боту.")
            return
        if _is_gpt_user(uid):
            await event.respond("GPT уже доступен для этого аккаунта.")
            return
        if admin_id_for_gpt is None:
            await event.respond("GPT сейчас недоступен: не задан BOT_ADMIN_ID.")
            return
        if uid in gpt_pending_users:
            await event.respond("Запрос на доступ к GPT уже отправлен админу, ожидайте решения.")
            return
        if not _append_pending_user(uid):
            await event.respond("Не удалось сохранить запрос на доступ к GPT.")
            return
        await event.respond("Запрос на доступ к GPT отправлен админу.")
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
        name_parts = []
        if sender is not None:
            first = (getattr(sender, "first_name", "") or "").strip()
            last = (getattr(sender, "last_name", "") or "").strip()
            uname = (getattr(sender, "username", "") or "").strip()
            if first or last:
                name_parts.append((first + " " + last).strip())
            if uname:
                name_parts.append(f"@{uname}")
        pretty = " / ".join(x for x in name_parts if x) or "(без имени)"
        text = (
            "Запрос доступа к GPT:\n"
            f"  id: {uid}\n"
            f"  пользователь: {pretty}\n\n"
            f"Выдать доступ: `/gpt_add {uid}`\n"
            "Отказать — просто проигнорировать это сообщение."
        )
        try:
            await client.send_message(int(admin_id_for_gpt), text)
        except Exception:
            # Если не удалось уведомить админа — молча игнорируем, чтобы не спамить пользователя.
            return

    @client.on(events.NewMessage(pattern=r"^/gpt_add\b"))
    async def on_admin_gpt_add(event: events.NewMessage.Event) -> None:
        """Админ: /gpt_add <user_id> — добавить пользователя в gpt-users.txt."""
        sid = event.sender_id
        if admin_id_for_gpt is None or sid is None or int(sid) != admin_id_for_gpt:
            return
        msg = (event.message.message or "").strip()
        parts = msg.split()
        if len(parts) != 2:
            await event.respond("Использование: /gpt_add <telegram_user_id>")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await event.respond("user_id должен быть числом.")
            return
        if uid in gpt_allowed_users:
            await event.respond(f"Пользователь {uid} уже имеет доступ к GPT.")
            _remove_pending_user(uid)
            return
        if not _append_gpt_user(uid):
            await event.respond("Не удалось обновить gpt-users.txt.")
            return
        _remove_pending_user(uid)
        await event.respond(
            f"Пользователь {uid} добавлен в gpt-users.txt и может пользоваться GPT.\n"
            f"Отозвать доступ: `/gpt_remove {uid}`"
        )
        try:
            await client.send_message(
                uid,
                "Доступ к GPT выдан. Теперь можно писать боту в личку обычным текстом (без /), и запрос уйдёт в GPT.",
            )
        except Exception:
            # Пользователь мог не начать диалог с ботом или запретить сообщения.
            pass

    @client.on(events.NewMessage(pattern=r"^/gpt_remove\b"))
    async def on_admin_gpt_remove(event: events.NewMessage.Event) -> None:
        """Админ: /gpt_remove <user_id> — удалить пользователя из gpt-users.txt."""
        sid = event.sender_id
        if admin_id_for_gpt is None or sid is None or int(sid) != admin_id_for_gpt:
            return
        msg = (event.message.message or "").strip()
        parts = msg.split()
        if len(parts) != 2:
            await event.respond("Использование: /gpt_remove <telegram_user_id>")
            return
        try:
            uid = int(parts[1])
        except ValueError:
            await event.respond("user_id должен быть числом.")
            return
        if uid == admin_id_for_gpt:
            await event.respond("Админа удалить нельзя: у него всегда есть доступ к GPT.")
            return
        if uid not in gpt_allowed_users:
            await event.respond(f"Пользователь {uid} не найден в gpt-users.txt.")
            _remove_pending_user(uid)
            return
        if not _remove_gpt_user(uid):
            await event.respond("Не удалось обновить gpt-users.txt.")
            return
        _remove_pending_user(uid)
        await event.respond(f"Пользователь {uid} удалён из gpt-users.txt.")
        try:
            await client.send_message(
                uid,
                "Доступ к GPT завершен. Спасибо за тестирование!",
            )
        except Exception:
            # Пользователь мог отключить/удалить диалог.
            pass

    @client.on(events.NewMessage(func=_gpt_message))
    async def on_gpt(event: events.NewMessage.Event) -> None:
        """Личка: текст GPT-пользователя → chat-agent (2×LLM) или прямой OpenAI."""
        msg = (event.message.message or "").strip()
        agent_base = _env("CHAT_AGENT_URL").rstrip("/")
        agent_secret = _env("CHAT_AGENT_SHARED_SECRET")

        if agent_base and agent_secret:
            import httpx
            import openai_gpt

            gpt_http = openai_gpt.http_timeout_sec()
            chat_timeout = max(fetch_timeout, gpt_http * 2.0 + 60.0, 120.0)
            status = await event.respond("Обрабатываю…")
            sender_id = int(event.sender_id or 0)
            is_admin = admin_id_for_gpt is not None and sender_id == admin_id_for_gpt
            url = f"{agent_base}/chat"
            try:
                async with httpx.AsyncClient(timeout=chat_timeout) as client_http:
                    r = await client_http.post(
                        url,
                        headers={"X-Chat-Agent-Secret": agent_secret},
                        json={
                            "user_id": str(event.sender_id),
                            "message": msg,
                            "include_env_system": not is_admin,
                        },
                    )
            except httpx.TimeoutException:
                logger.error("chat-agent request timed out after %.0fs", chat_timeout)
                await status.edit(
                    f"Таймаут {chat_timeout:.0f} с при запросе к chat-agent. "
                    "Проверьте CHAT_AGENT_LLM_TIMEOUT_SEC / сеть."
                )
                return
            except Exception:
                logger.exception("chat-agent HTTP failed")
                await status.edit("Не удалось связаться с chat-agent.")
                return
            if r.status_code == 401:
                await status.edit("Chat-agent: неверный секрет (X-Chat-Agent-Secret).")
                return
            try:
                data = r.json()
            except Exception:
                await status.edit("Chat-agent: неверный JSON в ответе.")
                return
            err = (data.get("error") or "").strip()
            out = (data.get("reply") or "").strip()
            if err:
                tail = err[:3500]
                await status.edit(f"Chat-agent: {tail}")
                return
            if not out:
                await status.edit("(пустой ответ chat-agent)")
                return
            chunks = split_for_telegram(out)
            send_kw = (
                {"parse_mode": "html"}
                if (data.get("reply_parse_mode") or "").strip().lower() == "html"
                else {}
            )
            try:
                await status.edit(chunks[0], **send_kw)
                for chunk in chunks[1:]:
                    await event.respond(chunk, **send_kw)
            except Exception:
                logger.warning(
                    "chat-agent: отправка с parse_mode не удалась, повтор без разметки",
                    exc_info=True,
                )
                await status.edit(chunks[0])
                for chunk in chunks[1:]:
                    await event.respond(chunk)
            return

        if not _env("OPENAI_API_KEY") or not _env("OPENAI_API_URL"):
            await event.respond(
                "GPT: задайте CHAT_AGENT_URL и CHAT_AGENT_SHARED_SECRET "
                "или OPENAI_API_KEY и OPENAI_API_URL."
            )
            return
        import openai_gpt

        status = await event.respond("Запрос к GPT…")
        gpt_http = openai_gpt.http_timeout_sec()
        gpt_timeout = max(fetch_timeout, gpt_http + 30.0)
        try:
            gpt_uid = (
                str(event.sender_id) if event.sender_id is not None else None
            )
            sender_id = int(event.sender_id or 0)
            is_admin = admin_id_for_gpt is not None and sender_id == admin_id_for_gpt

            async def _stream_and_collect() -> tuple[int, str]:
                loop = asyncio.get_running_loop()
                stream_q: asyncio.Queue[str] = asyncio.Queue()

                def _on_delta(piece: str) -> None:
                    loop.call_soon_threadsafe(stream_q.put_nowait, piece)

                stream_task = asyncio.create_task(
                    asyncio.to_thread(
                        openai_gpt.stream_chat_completion,
                        msg,
                        user_id=gpt_uid,
                        on_delta=_on_delta,
                        include_env_system=not is_admin,
                    )
                )
                out_local = ""
                last_edit = loop.time()
                while True:
                    if stream_task.done() and stream_q.empty():
                        break
                    try:
                        piece = await asyncio.wait_for(stream_q.get(), timeout=0.4)
                    except asyncio.TimeoutError:
                        continue
                    out_local += piece
                    now = loop.time()
                    if now - last_edit >= 1.0:
                        preview = split_for_telegram(out_local)
                        if preview and preview[0].strip():
                            await status.edit(preview[0])
                            last_edit = now
                code_local, final_out = await stream_task
                if final_out:
                    out_local = final_out
                return code_local, out_local

            code, out = await asyncio.wait_for(
                _stream_and_collect(), timeout=gpt_timeout
            )
        except asyncio.TimeoutError:
            logger.error("chat_completion timed out after %.0fs", gpt_timeout)
            await status.edit(
                f"Таймаут {gpt_timeout:.0f} с при запросе к GPT. "
                "Увеличьте OPENAI_HTTP_TIMEOUT_SEC (и при необходимости BOT_FETCH_TIMEOUT_SEC)."
            )
            return
        except Exception:
            logger.exception("chat_completion failed")
            await status.edit("Не удалось выполнить запрос к GPT.")
            return
        if code != 0:
            tail = (out or "ошибка")[:3500]
            await status.edit(f"GPT: {tail}")
            return
        if not (out or "").strip():
            await status.edit("(пустой ответ GPT)")
            return
        chunks = split_for_telegram(out)
        await status.edit(chunks[0])
        for chunk in chunks[1:]:
            await event.respond(chunk)

    logger.info("Telethon polling (bot)…")
    await client.start(bot_token=bot_token)
    await client.run_until_disconnected()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not _credentials_ok():
        print(
            "Нужны TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
