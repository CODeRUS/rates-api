# -*- coding: utf-8 -*-
"""RUB/USDT P2P для сводки: Bybit с автопереключением на HTX при пустой витрине."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rates_primitives import read_bybit_p2p, read_htx_p2p

SUMMARY_P2P_FALLBACK_NOTE = (
    "Bybit P2P недоступен — для сводки использован HTX P2P (RUB/USDT)"
)

WARN_BYBIT_NO_CASH = (
    "Bybit: нет объявлений Cash Deposit (18) с completion≥99 "
    "(100 USDT, minAmount≥100·price)"
)
WARN_BYBIT_NO_TRANSFER = (
    "Bybit: нет объявлений только перевод (14, без 18) с completion≥99 "
    "(100 USDT, minAmount≥100·price)"
)
WARN_HTX_NO_CASH = (
    "HTX: нет объявлений с наличными под фильтры (100 USDT, minTradeLimit≥100·price)"
)
WARN_HTX_NO_TRANSFER = (
    "HTX: нет объявлений без наличных под фильтры (100 USDT, minTradeLimit≥100·price)"
)


@dataclass(frozen=True)
class SummaryP2pRubLegs:
    cash_rub_per_usdt: Optional[float]
    transfer_rub_per_usdt: Optional[float]
    cash_via_htx: bool = False
    transfer_via_htx: bool = False
    warnings: Tuple[str, ...] = ()


def _positive(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _build_summary_legs(
    c_b: Optional[float],
    t_b: Optional[float],
    c_h: Optional[float],
    t_h: Optional[float],
    *,
    tried_htx: bool,
) -> SummaryP2pRubLegs:
    warnings: List[str] = []
    cash = _positive(c_b)
    transfer = _positive(t_b)
    cash_htx = transfer_htx = False
    used_htx = False

    if cash is None:
        ch = _positive(c_h)
        if ch is not None:
            cash = ch
            cash_htx = True
            used_htx = True
    if transfer is None:
        th = _positive(t_h)
        if th is not None:
            transfer = th
            transfer_htx = True
            used_htx = True

    if used_htx:
        warnings.append(SUMMARY_P2P_FALLBACK_NOTE)

    if cash is None:
        warnings.append(WARN_HTX_NO_CASH if tried_htx else WARN_BYBIT_NO_CASH)
    if transfer is None:
        warnings.append(WARN_HTX_NO_TRANSFER if tried_htx else WARN_BYBIT_NO_TRANSFER)

    # Dedupe while preserving order
    seen: set[str] = set()
    uniq: List[str] = []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            uniq.append(w)

    return SummaryP2pRubLegs(
        cash_rub_per_usdt=cash,
        transfer_rub_per_usdt=transfer,
        cash_via_htx=cash_htx,
        transfer_via_htx=transfer_htx,
        warnings=tuple(uniq),
    )


def read_summary_p2p_rub_legs(doc: Optional[Dict[str, object]]) -> SummaryP2pRubLegs:
    if not doc:
        return SummaryP2pRubLegs(None, None)

    c_b, t_b, _w_b = read_bybit_p2p(doc)
    need_htx = _positive(c_b) is None or _positive(t_b) is None
    c_h = t_h = None
    if need_htx:
        c_h, t_h, _w_h = read_htx_p2p(doc)
    return _build_summary_legs(c_b, t_b, c_h, t_h, tried_htx=need_htx)


def fetch_summary_p2p_rub_legs_live() -> SummaryP2pRubLegs:
    from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp
    from sources.htx_bitkub import htx_p2p_usdt_rub as hx

    c_b = t_b = None
    try:
        ia, ib = bp.fetch_best_cash_and_bank_transfer_items(
            size=20,
            verification_filter=0,
            target_usdt=bp.DEFAULT_TARGET_USDT,
            min_completion=99.0,
        )
        if ia:
            c_b = float(ia["price"])
        if ib:
            t_b = float(ib["price"])
    except RuntimeError:
        pass

    need_htx = _positive(c_b) is None or _positive(t_b) is None
    c_h = t_h = None
    if need_htx:
        try:
            ha, hb = hx.fetch_best_cash_and_non_cash_offers(max_pages=30)
            if ha:
                c_h = float(ha["price"])
            if hb:
                t_h = float(hb["price"])
        except RuntimeError:
            pass
    return _build_summary_legs(c_b, t_b, c_h, t_h, tried_htx=need_htx)


def cash_label(*, via_htx: bool, dest: str) -> str:
    if via_htx:
        return f"HTX P2P (наличные) → {dest}"
    return f"Bybit P2P (cash) → {dest}"


def transfer_label(*, via_htx: bool, dest: str) -> str:
    if via_htx:
        return f"HTX P2P (перевод) → {dest}"
    return f"Bybit P2P (перевод) → {dest}"


def cash_merge_key(via_htx: bool) -> str:
    return "htx_cash" if via_htx else "bybit_cash"


def transfer_merge_key(via_htx: bool) -> str:
    return "htx_no_cash" if via_htx else "bybit_transfer"


def min_rub_per_usdt(legs: SummaryP2pRubLegs) -> Tuple[Optional[float], bool]:
    """Мин. RUB/USDT и флаг «минимум с HTX»."""
    opts: List[Tuple[float, bool]] = []
    if legs.cash_rub_per_usdt is not None:
        opts.append((float(legs.cash_rub_per_usdt), legs.cash_via_htx))
    if legs.transfer_rub_per_usdt is not None:
        opts.append((float(legs.transfer_rub_per_usdt), legs.transfer_via_htx))
    if not opts:
        return None, False
    price, via_htx = min(opts, key=lambda x: x[0])
    return price, via_htx


def combo_p2p_label(*, via_htx: bool, suffix: str) -> str:
    brand = "HTX P2P" if via_htx else "Bybit P2P"
    return f"{brand} → {suffix}"
