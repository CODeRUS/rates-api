# -*- coding: utf-8 -*-
"""
Разбор витрины курсов TT Exchange (наличные фиаты → THB) без импорта rates_sources.
Используется пакетом ``sources.ttexchange`` и отчётом ``exchange_report``.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, List, Optional, Set, Tuple


def normalize_ttexchange_branch_label(raw: str) -> str:
    """
    Короткое имя филиала для отчётов: без кода слева от ``:``, без хвоста ``Branch``.
    ``NK2 : Naklua 2 Branch`` → ``Naklua 2``.
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if ":" in s:
        s = s.split(":", 1)[1].strip()
    s = re.sub(r"\s+Branch\s*$", "", s, flags=re.IGNORECASE).strip()
    return s


def _branch_display_name(stores: Any, branch_id: str) -> str:
    if not isinstance(stores, list):
        return normalize_ttexchange_branch_label(str(branch_id))
    for row in stores:
        if isinstance(row, dict) and str(row.get("branch_id")) == str(branch_id):
            name = row.get("name")
            if name:
                return normalize_ttexchange_branch_label(str(name))
    return normalize_ttexchange_branch_label(str(branch_id))


def _row_buy_rate_for_pick(row: Any) -> float:
    try:
        v = float(row.get("current_buy_rate"))  # type: ignore[union-attr]
        return v if v > 0 else float("-inf")
    except (TypeError, ValueError):
        return float("-inf")


def _unique_nonempty_ordered(strings: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
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


def fiat_buy_thb_per_unit(currencies: Any, code: str) -> Optional[float]:
    """THB за 1 ед. ``code`` (USD/EUR/CNY/…) по правилам :func:`_pick_currency_row`."""
    row, _note, _omit = _pick_currency_row(currencies, code)
    if not row:
        return None
    raw = row.get("current_buy_rate")
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None
