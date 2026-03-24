# -*- coding: utf-8 -*-
"""
Наличные ttexchange: курс RUB за 1 THB по витрине филиала (API как CLI ``rates``).
"""
from __future__ import annotations

from typing import Any, List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "ttexchange"
EMOJI = "🏧"
IS_BASELINE = False
CATEGORY = SourceCategory.CASH


def help_text() -> str:
    return (
        "TT Exchange: наличные RUB/THB по API филиала; CLI — полный клиент ttexchange_api "
        "(stores, rates, …). См. ttexchange --help."
    )


def command(argv: list[str]) -> int:
    from .ttexchange_api import cli_main

    return cli_main(argv)


def _branch_display_name(stores: Any, branch_id: str) -> str:
    if not isinstance(stores, list):
        return branch_id
    for row in stores:
        if isinstance(row, dict) and str(row.get("branch_id")) == str(branch_id):
            name = row.get("name")
            if name:
                return str(name)
    return branch_id


def _pick_rub_row(currencies: Any) -> Optional[Any]:
    if not isinstance(currencies, list):
        return None
    candidates = []
    for row in currencies:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if name == "RUB" or name.startswith("RUB"):
            candidates.append(row)
    if not candidates:
        return None
    for row in candidates:
        if str(row.get("name") or "").strip() == "RUB":
            return row
    return candidates[0]


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import ttexchange_api as ttx

    try:
        stores = ttx.get_stores("ru")
        bid = ttx._pick_default_branch_id(stores)
        if not bid:
            ctx.warnings.append("ttexchange: не удалось определить branch_id")
            return None
        branch_name = _branch_display_name(stores, bid)
        cur = ttx.get_currencies(bid, is_main=False)
        rub_row = _pick_rub_row(cur)
        if not rub_row:
            ctx.warnings.append("ttexchange: нет строки RUB в курсах филиала")
            return None
        buy = rub_row.get("current_buy_rate")
        if buy is None:
            ctx.warnings.append("ttexchange: нет current_buy_rate для RUB")
            return None
        thb_per_rub = float(buy)
        if thb_per_rub <= 0:
            ctx.warnings.append("ttexchange: некорректный current_buy_rate для RUB")
            return None
        rub_per_thb = 1.0 / thb_per_rub
        desc = rub_row.get("description")
        note = "наличные, покупка RUB (THB за 1 RUB) → RUB/THB"
        if desc:
            note = f"{note}; {desc}"
        return [
            SourceQuote(
                rub_per_thb,
                f"ttexchange (филиал {branch_name})",
                note=note,
            )
        ]
    except Exception as e:
        ctx.warnings.append(f"ttexchange: {e}")
        return None
