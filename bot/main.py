#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот (Telethon): /rates, /usdt, /cash.

Переменные можно задать в файле ``.env`` в корне репозитория (подхватывается при старте).

Запуск из корня репозитория::

    export TELEGRAM_API_ID="611335"
    export TELEGRAM_API_HASH="…"
    export TELEGRAM_BOT_TOKEN="…"
    export BOT_ADMIN_ID="123456789"  # опционально: Telegram user id — только он может /refresh
    python -m bot.main

Секреты не коммитьте. Файл сессии: ``bot/rates_bot.session`` (в .gitignore).
Опционально ``BOT_ADMIN_ID``: ``/refresh`` — сброс кеша сводки; ``/refresh usdt`` — сброс кеша отчёта USDT.
Опционально ``BOT_FETCH_TIMEOUT_SEC`` (по умолчанию 180): таймаут сборки сводки/USDT/cash в потоке.
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

from bot.summary_adapter import (
    get_cash_text,
    get_summary_text,
    get_usdt_text,
    split_for_telegram,
)

logger = logging.getLogger(__name__)


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

    async def _send_rates_summary(event: events.NewMessage.Event, *, refresh: bool) -> None:
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
                "Уже выполняется запрос (/rates, /usdt или /cash). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт получение…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(get_summary_text, refresh=refresh),
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
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    async def _send_cash_report(event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                busy = True
            else:
                busy = False
                rates_busy_chats.add(chat_id)
        if busy:
            await event.respond(
                "Уже выполняется запрос (/rates, /usdt или /cash). Дождитесь результата."
            )
            return
        try:
            status_msg = await event.respond("Идёт получение cash…")
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(get_cash_text, refresh=False, top_n=3),
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
                "Уже выполняется запрос (/rates, /usdt или /cash). Дождитесь результата."
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
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    @client.on(events.NewMessage(pattern=r"(?i)^/start(?:@\S+)?$"))
    async def on_start(event: events.NewMessage.Event) -> None:
        await event.respond(
            "Команды:\n"
            "/rates — сводка RUB/THB\n"
            "/usdt — P2P RUB/USDT и USDT/THB\n"
            "/cash — наличные РБК (Москва, СПб) и пары ➔ THB через TT Exchange"
        )

    @client.on(events.NewMessage(pattern=r"(?i)^/cash(?:@\S+)?$"))
    async def on_cash(event: events.NewMessage.Event) -> None:
        await _send_cash_report(event)

    @client.on(events.NewMessage(pattern=r"(?i)^/usdt(?:@\S+)?$"))
    async def on_usdt(event: events.NewMessage.Event) -> None:
        await _send_usdt_report(event, refresh=False)

    @client.on(events.NewMessage(pattern=r"(?i)^/rates(?:@\S+)?"))
    async def on_rates(event: events.NewMessage.Event) -> None:
        msg = event.message.message or ""
        tokens = msg.split()
        refresh = (
            len(tokens) > 1
            and tokens[1].lower() in ("refresh", "r", "--refresh")
        )
        await _send_rates_summary(event, refresh=refresh)

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
        if len(tokens) > 1:
            await event.respond("Неизвестная подкоманда. Для USDT: /refresh usdt")
            return
        logger.info("Admin /refresh from sender_id=%s", sender)
        await _send_rates_summary(event, refresh=True)

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
