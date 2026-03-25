# -*- coding: utf-8 -*-
"""
Наличные ttexchange: курс RUB за 1 THB по витрине филиала (API как CLI ``rates``).
"""
from __future__ import annotations

import math
from typing import Any, Iterable, List, Optional, Tuple

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "ttexchange"
EMOJI = "•"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_FIAT_CASH_CATEGORY = {
    "RUB": SourceCategory.CASH_RUB,
    "USD": SourceCategory.CASH_USD,
    "EUR": SourceCategory.CASH_EUR,
    "CNY": SourceCategory.CASH_CNY,
}

_FIAT_ORDER = ("RUB", "USD", "EUR", "CNY")


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


def _row_buy_rate_for_pick(row: Any) -> float:
    try:
        v = float(row.get("current_buy_rate"))  # type: ignore[union-attr]
        return v if v > 0 else float("-inf")
    except (TypeError, ValueError):
        return float("-inf")


def _unique_nonempty_ordered(strings: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for raw in strings:
        s = str(raw).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _pick_currency_row(currencies: Any, code: str) -> Tuple[Optional[Any], str, bool]:
    """
    Строка API для курса, опциональный фрагмент note по номиналам, флаг «без номиналов».

    Если у **всех** тиров валюты один и тот же ``current_buy_rate``, номиналы в note
    не выводим (ни склейка, ни ``description`` строки — только филиал снаружи).

    Иначе берём максимальный курс; если на нём несколько строк — перечисляем
    ``description``/``name`` как раньше.
    """
    if not isinstance(currencies, list):
        return None, "", False
    code = code.strip().upper()
    candidates: List[Any] = []
    for row in currencies:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip().upper()
        if code == "RUB":
            if name == "RUB" or name.startswith("RUB"):
                candidates.append(row)
            continue
        if code == "USD":
            if name == "USDT" or name.startswith("USDT"):
                continue
            if name == "USD" or name.startswith("USD"):
                candidates.append(row)
            continue
        if code in ("EUR", "CNY"):
            if name == code or name.startswith(f"{code}(") or name.startswith(code):
                candidates.append(row)
            continue
    if not candidates:
        return None, "", False
    if code == "RUB":
        for row in candidates:
            if str(row.get("name") or "").strip().upper() == "RUB":
                return row, "", False
        return candidates[0], "", False

    rates = [_row_buy_rate_for_pick(r) for r in candidates]
    finite = [x for x in rates if x > float("-inf")]
    all_tiers_same_rate = (
        len(candidates) >= 2
        and len(finite) == len(candidates)
        and min(finite) == max(finite)
    )
    if all_tiers_same_rate:
        return candidates[0], "", True

    best = max(finite) if finite else float("-inf")
    at_best = [
        r
        for r in candidates
        if math.isclose(_row_buy_rate_for_pick(r), best, rel_tol=0.0, abs_tol=1e-9)
        and _row_buy_rate_for_pick(r) > float("-inf")
    ]
    if not at_best:
        return None, "", False
    rep = at_best[0]
    tier_note = ""
    if len(at_best) > 1:
        descs = _unique_nonempty_ordered(
            str(r.get("description") or "") for r in at_best
        )
        if len(descs) >= 2:
            tier_note = " · ".join(descs)
        else:
            names = _unique_nonempty_ordered(str(r.get("name") or "") for r in at_best)
            if len(names) >= 2:
                tier_note = " · ".join(names)
    return rep, tier_note, False


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
        quotes: List[SourceQuote] = []
        for fiat in _FIAT_ORDER:
            row, tier_note, omit_denoms = _pick_currency_row(cur, fiat)
            cat = _FIAT_CASH_CATEGORY.get(fiat)
            if not row or cat is None:
                continue
            buy = row.get("current_buy_rate")
            if buy is None:
                continue
            try:
                thb_per = float(buy)
            except (TypeError, ValueError):
                continue
            if thb_per <= 0:
                continue
            if fiat == "RUB":
                rate = 1.0 / thb_per
                compare = True
                parts = []
            else:
                rate = thb_per
                compare = False
                parts = []
            if not omit_denoms:
                if tier_note:
                    parts.append(tier_note)
                else:
                    desc = row.get("description")
                    if desc:
                        parts.append(str(desc))
            if branch_name:
                parts.append(branch_name)
            note = " · ".join(parts)
            quotes.append(
                SourceQuote(
                    rate,
                    "TT Currency Exchange",
                    note=note,
                    category=cat,
                    compare_to_baseline=compare,
                )
            )
        if not quotes:
            ctx.warnings.append("ttexchange: нет строк наличных RUB/USD/EUR/CNY в курсах филиала")
            return None
        return quotes
    except Exception as e:
        ctx.warnings.append(f"ttexchange: {e}")
        return None
