# -*- coding: utf-8 -*-
"""
Источник котировок из unified cache, который наполняет `userbot`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, TYPE_CHECKING

import rates_unified_cache as ucc
from rates_categories import SourceCategory

if TYPE_CHECKING:
    from rates_sources import FetchContext

SOURCE_ID = "userbot_cash"
EMOJI = "💬"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


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
            c = _cat(str(row.get("category") or ""))
            if c is None:
                continue
            try:
                rate = float(row.get("rate") or 0)
            except (TypeError, ValueError):
                continue
            if rate <= 0:
                continue
            src_name = str(row.get("source_name") or row.get("source_id") or "Userbot")
            cur = str(row.get("currency") or "").upper()
            label = src_name if not cur else f"{src_name} {cur}"
            note = f"msg #{row.get('message_id')}" if row.get("message_id") is not None else ""
            out.append(
                SourceQuote(
                    rate=rate,
                    label=label,
                    note=note,
                    category=c,
                    emoji="•",
                    compare_to_baseline=(c == SourceCategory.CASH_RUB),
                )
            )
    return out or None

