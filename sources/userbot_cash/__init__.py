# -*- coding: utf-8 -*-
"""
Источник котировок из unified cache, который наполняет `userbot`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

import rates_unified_cache as ucc
from rates_categories import SourceCategory
from userbot.sources_config import USERBOT_SOURCES

if TYPE_CHECKING:
    from rates_sources import FetchContext

SOURCE_ID = "userbot_cash"
EMOJI = "💬"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER
_SUMMARY_NOTE_BY_SOURCE: Dict[str, str] = {
    str(cfg.source_id): str(cfg.summary_note or "").strip() for cfg in USERBOT_SOURCES
}
_EMOJI_BY_SOURCE: Dict[str, str] = {
    str(cfg.source_id): str(cfg.emoji or "").strip() for cfg in USERBOT_SOURCES
}


def help_text() -> str:
    return (
        "Котировки из Telegram-каналов (userbot → unified cache). "
        "Категория задается в конфиге userbot."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _cat(raw: str) -> Optional[SourceCategory]:
    s = (raw or "").strip().lower()
    mp = {
        "transfer": SourceCategory.TRANSFER,
        "cash_rub": SourceCategory.CASH_RUB,
        "cash_usd": SourceCategory.CASH_USD,
        "cash_eur": SourceCategory.CASH_EUR,
        "cash_cny": SourceCategory.CASH_CNY,
    }
    return mp.get(s)


def summary(ctx: "FetchContext") -> Optional[List[Any]]:
    from rates_sources import SourceQuote

    doc = ucc.load_unified()
    l1 = doc.get("l1") or {}
    if not isinstance(l1, dict):
        return None
    out: List[SourceQuote] = []
    # В `rates.py summary` скрываем Telegram-cash источники именно из блоков:
    # "Наличные USD ➔ THB", "Наличные EUR ➔ THB", "Наличные CNY ➔ THB".
    # Команда `cash` (и /cash у бота) читает unified-cache напрямую и не зависит от этого плагина.
    hidden_cash_thb_cats = {
        SourceCategory.CASH_USD,
        SourceCategory.CASH_EUR,
        SourceCategory.CASH_CNY,
    }
    for key in sorted(l1.keys()):
        if not str(key).startswith("chatcash:"):
            continue
        hit = ucc.l1_get_valid(doc, str(key))
        if hit is None:
            continue
        payload = hit[1]
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("source_id") or "").strip()
            # Для Unired есть отдельный source-плагин `unired_bkb` в summary —
            # не дублируем его из userbot_cash.
            if sid == "unired_bkb":
                continue
            c = _cat(str(row.get("category") or ""))
            if c is None:
                continue
            if c in hidden_cash_thb_cats:
                continue
            try:
                rate = float(row.get("rate") or 0)
            except (TypeError, ValueError):
                continue
            if rate <= 0:
                continue
            src_name = str(row.get("source_name") or row.get("source_id") or "Userbot")
            cur = str(row.get("currency") or "").upper()
            # Для transfer-источников показываем только имя источника (без суффикса валюты).
            label = src_name if (c == SourceCategory.TRANSFER or not cur) else f"{src_name} {cur}"
            note_cfg = _SUMMARY_NOTE_BY_SOURCE.get(sid, "")
            note = note_cfg if note_cfg else (f"msg #{row.get('message_id')}" if row.get("message_id") is not None else "")
            emoji_cfg = _EMOJI_BY_SOURCE.get(sid, "")
            emoji = emoji_cfg if emoji_cfg else "•"
            out.append(
                SourceQuote(
                    rate=rate,
                    label=label,
                    note=note,
                    category=c,
                    emoji=emoji,
                    compare_to_baseline=(c in {SourceCategory.TRANSFER, SourceCategory.CASH_RUB}),
                )
            )
    return out or None

