# -*- coding: utf-8 -*-
"""Guest mode: one text reply when the bot is mentioned."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field

from bot.calc_args import parse_calc_command_args
from bot.rates_tokens import parse_rates_command_tokens
from bot.rshb_args import parse_rshb_command_args

logger = logging.getLogger(__name__)

GUEST_TEXT_LIMIT = 4096
_DEFAULT_FETCH_TIMEOUT_SEC = 15.0

GUEST_DENIED = "Эта команда в гостевом режиме недоступна."
GUEST_NOT_READY = "Сводка ещё не готова."
GUEST_FAILED = "Не удалось собрать ответ. Попробуйте позже."
GUEST_EMPTY = "(пустой ответ)"

_KNOWN = frozenset(
    {
        "rates",
        "usdt",
        "cash",
        "exchange",
        "calc",
        "rshb",
        "refresh",
        "gpt",
        "gpt_add",
        "gpt_remove",
    }
)
_DENIED = frozenset({"refresh", "gpt", "gpt_add", "gpt_remove"})
_CASH_SOURCES = frozenset({"all", "banki", "vbr", "rbc"})
_BACKGROUND_KIND = {
    "rates": "summary",
    "usdt": "usdt",
    "cash": "cash",
    "exchange": "exchange",
}


@dataclass
class GuestCommand:
    kind: str
    reply: str = ""
    payload: dict = field(default_factory=dict)


def guest_fetch_timeout_sec() -> float:
    raw = (os.environ.get("GUEST_FETCH_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_FETCH_TIMEOUT_SEC
    try:
        return max(1.0, float(raw))
    except ValueError:
        return _DEFAULT_FETCH_TIMEOUT_SEC


def fit_guest_text(text: str, limit: int = GUEST_TEXT_LIMIT) -> str:
    """Cut on a line boundary so the text fits in one Telegram message."""
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    nl = cut.rfind("\n")
    if nl > 0:
        return cut[:nl].rstrip()
    return cut


def strip_bot_mention(text: str, bot_username: str) -> str:
    """Remove the bot @username anywhere in the text, including a ``/cmd@username`` token."""
    name = (bot_username or "").strip().lstrip("@")
    src = text or ""
    if not name:
        return re.sub(r"\s+", " ", src).strip()
    src = re.sub(
        rf"(?i)/([A-Za-z0-9_]+)@{re.escape(name)}\b",
        r"/\1",
        src,
    )
    src = re.sub(rf"(?i)(?<!\w)@{re.escape(name)}\b", " ", src)
    return re.sub(r"\s+", " ", src).strip()


def _command_name(token: str) -> str:
    body = token[1:] if token.startswith("/") else token
    return body.split("@", 1)[0].lower()


def prepare_guest_command(text: str, bot_username: str) -> GuestCommand:
    cleaned = strip_bot_mention(text, bot_username)
    if not cleaned:
        return GuestCommand(kind="ignore")
    tokens = cleaned.split()
    name = _command_name(tokens[0])
    if name not in _KNOWN:
        return GuestCommand(kind="ignore")
    if name in _DENIED:
        return GuestCommand(kind="deny", reply=GUEST_DENIED)
    normalized = "/" + name + ((" " + " ".join(tokens[1:])) if len(tokens) > 1 else "")
    norm_tokens = normalized.split()
    if name == "rates":
        _refresh, output_filter, receiving_thb = parse_rates_command_tokens(norm_tokens)
        return GuestCommand(
            kind="rates",
            payload={
                "output_filter": output_filter,
                "receiving_thb": receiving_thb,
            },
        )
    if name == "usdt":
        return GuestCommand(kind="usdt")
    if name == "cash":
        return _prepare_cash(norm_tokens)
    if name == "exchange":
        return _prepare_exchange(norm_tokens)
    if name == "calc":
        try:
            budget_rub, fiat_code, rub_per_fiat = parse_calc_command_args(normalized)
        except ValueError as exc:
            hint = str(exc).strip() or "Формат: /calc RUB usd|eur|cny КУРС"
            return GuestCommand(kind="error", reply=hint)
        return GuestCommand(
            kind="calc",
            payload={
                "budget_rub": budget_rub,
                "fiat_code": fiat_code,
                "rub_per_fiat": rub_per_fiat,
            },
        )
    try:
        thb_nets, atm_fee = parse_rshb_command_args(normalized)
    except ValueError:
        return GuestCommand(
            kind="error",
            reply=(
                "Формат: /rshb [THB] [ATM_FEE] или /rshb 30000 20000 10000 250 "
                "(несколько снятий, последнее число — комиссия ATM)."
            ),
        )
    return GuestCommand(
        kind="rshb",
        payload={"thb_nets": thb_nets, "atm_fee": atm_fee},
    )


def _prepare_cash(tokens: list[str]) -> GuestCommand:
    import cash_report as cash_mod

    city_n: int | None = None
    top_n = 3
    if len(tokens) > 1:
        try:
            city_n = int(tokens[1])
        except ValueError:
            return GuestCommand(
                kind="error",
                reply="После /cash укажите номер города из списка, например: /cash 1",
            )
    rest = tokens[2:]
    i = 0
    source_spec: str | None = None
    if i < len(rest):
        low = rest[i].lower()
        if low in _CASH_SOURCES:
            source_spec = low
            i += 1
        elif rest[i].isdigit():
            top_n = int(rest[i])
            i += 1
    if i < len(rest):
        if rest[i].isdigit():
            top_n = min(int(rest[i]), 50)
        elif rest[i].lower() in _CASH_SOURCES and source_spec is None:
            source_spec = rest[i].lower()
    use_rbc, use_banki, use_vbr = True, True, True
    if source_spec:
        try:
            use_rbc, use_banki, use_vbr = cash_mod.parse_cash_sources_str(source_spec)
        except ValueError as exc:
            return GuestCommand(kind="error", reply=f"Источник: {exc}")
    if top_n < 1:
        return GuestCommand(kind="error", reply="Число строк top должно быть не меньше 1.")
    top_n = min(top_n, 50)
    if city_n is None:
        return GuestCommand(kind="cash_cities")
    cities = [x[0] for x in cash_mod._CASH_LOCATIONS]
    if city_n < 1 or city_n > len(cities):
        return GuestCommand(
            kind="error",
            reply=f"Номер города должен быть от 1 до {len(cities)}.",
        )
    return GuestCommand(
        kind="cash",
        payload={
            "city_label": cities[city_n - 1],
            "top_n": top_n,
            "use_rbc": use_rbc,
            "use_banki": use_banki,
            "use_vbr": use_vbr,
        },
    )


def _prepare_exchange(tokens: list[str]) -> GuestCommand:
    top_n = 10
    if len(tokens) > 1:
        try:
            top_n = int(tokens[1])
        except ValueError:
            return GuestCommand(
                kind="error",
                reply="После /exchange укажите число филиалов, например: /exchange 5",
            )
        if top_n < 1:
            return GuestCommand(
                kind="error",
                reply="Число филиалов должно быть не меньше 1.",
            )
        top_n = min(top_n, 50)
    return GuestCommand(kind="exchange", payload={"top_n": top_n})


def render_guest_command(cmd: GuestCommand) -> tuple[str, str | None]:
    """Build cached report text. The second value is the stale-L2 background refresh kind."""
    from bot.summary_adapter import (
        get_calc_text,
        get_cash_cities_text,
        get_cash_text,
        get_exchange_text,
        get_rshb_text,
        get_summary_text,
        get_usdt_text,
    )

    if cmd.kind == "rates":
        text = get_summary_text(
            refresh=False,
            output_filter=cmd.payload.get("output_filter") or "",
            receiving_thb=cmd.payload.get("receiving_thb"),
        )
        getter = get_summary_text
    elif cmd.kind == "usdt":
        text = get_usdt_text(refresh=False)
        getter = get_usdt_text
    elif cmd.kind == "cash_cities":
        return get_cash_cities_text(), None
    elif cmd.kind == "cash":
        text = get_cash_text(
            refresh=False,
            top_n=int(cmd.payload["top_n"]),
            city_label=cmd.payload["city_label"],
            use_rbc=bool(cmd.payload["use_rbc"]),
            use_banki=bool(cmd.payload["use_banki"]),
            use_vbr=bool(cmd.payload["use_vbr"]),
        )
        getter = get_cash_text
    elif cmd.kind == "exchange":
        text = get_exchange_text(
            refresh=False,
            top_n=int(cmd.payload["top_n"]),
            lang="ru",
        )
        getter = get_exchange_text
    elif cmd.kind == "calc":
        text = get_calc_text(
            budget_rub=float(cmd.payload["budget_rub"]),
            fiat_code=cmd.payload["fiat_code"],
            rub_per_fiat=float(cmd.payload["rub_per_fiat"]),
            refresh=False,
        )
        return text, None
    elif cmd.kind == "rshb":
        text = get_rshb_text(
            thb_nets=list(cmd.payload["thb_nets"]),
            atm_fee=float(cmd.payload["atm_fee"]),
        )
        return text, None
    else:
        return cmd.reply, None
    background = _BACKGROUND_KIND.get(cmd.kind)
    if not getattr(getter, "_needs_background_refresh", False):
        background = None
    return text, background


def _sender_id(message: object) -> int | None:
    from_id = getattr(message, "from_id", None)
    user_id = getattr(from_id, "user_id", None)
    if user_id is None:
        return None
    return int(user_id)


async def answer_guest(client: object, query_id: int, text: str) -> None:
    from telethon.tl.functions.messages import SetBotGuestChatResultRequest
    from telethon.tl.types import InputBotInlineMessageText, InputBotInlineResult

    body = fit_guest_text(text).strip() or GUEST_EMPTY
    result = InputBotInlineResult(
        id=str(query_id),
        type="article",
        title="Курсы",
        send_message=InputBotInlineMessageText(message=body, no_webpage=True),
    )
    await client(SetBotGuestChatResultRequest(query_id=query_id, result=result))  # type: ignore[operator]


async def handle_guest_update(client: object, update: object, bot_username: str) -> None:
    from telethon.tl import types as tl_types

    guest_cls = getattr(tl_types, "UpdateBotGuestChatQuery", None)
    if guest_cls is None or not isinstance(update, guest_cls):
        return
    query_id = int(update.query_id)
    raw = getattr(update.message, "message", None) or ""
    sender = _sender_id(update.message)
    try:
        cmd = prepare_guest_command(raw, bot_username)
        logger.info(
            "guest query_id=%s sender=%s kind=%s",
            query_id,
            sender,
            cmd.kind,
        )
        if cmd.kind == "ignore":
            return
        if cmd.reply:
            text = cmd.reply
            background = None
        else:
            text, background = await asyncio.wait_for(
                asyncio.to_thread(render_guest_command, cmd),
                timeout=guest_fetch_timeout_sec(),
            )
        if background:
            from bot.summary_adapter import run_background_unified_refresh

            asyncio.create_task(asyncio.to_thread(run_background_unified_refresh, background))
    except asyncio.TimeoutError:
        logger.error(
            "guest query_id=%s timed out after %.0fs",
            query_id,
            guest_fetch_timeout_sec(),
        )
        text = GUEST_NOT_READY
    except Exception:
        logger.exception("guest query_id=%s failed", query_id)
        text = GUEST_FAILED
    try:
        await answer_guest(client, query_id, text)
    except Exception:
        logger.exception("guest answer failed query_id=%s", query_id)
