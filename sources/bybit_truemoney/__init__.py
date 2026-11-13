# -*- coding: utf-8 -*-
"""Bybit P2P RUB/USDT × TrueMoney THBT (Baht Swap на Base).

USDT→THBT — прямой quoter.
USDC→THBT — сначала Bybit spot ask USDCUSDT (USDT за 1 USDC), затем quoter.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from typing import Optional

from rates_categories import SourceCategory
from rates_p2p_rub_summary import (
    SummaryP2pRubLegs,
    cash_label,
    fetch_summary_p2p_rub_legs_live,
    read_summary_p2p_rub_legs,
    transfer_label,
)

from .bahtswap_quote import fetch_thbt_quote_payload, thbt_per_token
from .bybit_usdc_usdt import fetch_ask_payload, parse_ask

SOURCE_ID = "bybit_truemoney"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_DEST_USDT = "TrueMoney USDT"
_DEST_USDC = "TrueMoney USDC"


def help_text() -> str:
    return (
        "Bybit P2P USDT/RUB + TrueMoney THBT (Baht Swap, Base).\n"
        "  USDT→THBT — quoter Uniswap v4.\n"
        "  USDC→THBT — сначала ask Bybit USDCUSDT, затем quoter.\n"
        "Котировки сводки: rates.py bybit_truemoney summary"
    )


def command(argv: list[str]) -> int:
    print(help_text(), file=sys.stderr if argv else sys.stdout)
    return 0


def _pos_int(value: object) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _append_leg(
    out: list,
    *,
    rub: float,
    via_htx: bool,
    kind: str,
    thbt_usdt: Optional[Decimal],
    thbt_usdc: Optional[Decimal],
    ask: Optional[Decimal],
) -> None:
    from rates_sources import SourceQuote

    rub_d = Decimal(str(rub))
    label_for = cash_label if kind == "cash" else transfer_label
    if thbt_usdt is not None and thbt_usdt > 0:
        out.append(SourceQuote(float(rub_d / thbt_usdt), label_for(via_htx=via_htx, dest=_DEST_USDT)))
    if thbt_usdc is not None and thbt_usdc > 0 and ask is not None and ask > 0:
        out.append(
            SourceQuote(
                float(rub_d * ask / thbt_usdc),
                label_for(via_htx=via_htx, dest=_DEST_USDC),
            )
        )


def quotes_from_legs(
    legs: SummaryP2pRubLegs,
    *,
    usdt_amount_out: object,
    usdc_amount_out: object,
    ask_usdt_per_usdc: object,
) -> Optional[list]:
    usdt_raw = _pos_int(usdt_amount_out)
    usdc_raw = _pos_int(usdc_amount_out)
    thbt_usdt = thbt_per_token(usdt_raw) if usdt_raw is not None else None
    thbt_usdc = thbt_per_token(usdc_raw) if usdc_raw is not None else None
    ask = parse_ask(ask_usdt_per_usdc)
    out: list = []
    if legs.cash_rub_per_usdt is not None and legs.cash_rub_per_usdt > 0:
        _append_leg(
            out,
            rub=float(legs.cash_rub_per_usdt),
            via_htx=legs.cash_via_htx,
            kind="cash",
            thbt_usdt=thbt_usdt,
            thbt_usdc=thbt_usdc,
            ask=ask,
        )
    if legs.transfer_rub_per_usdt is not None and legs.transfer_rub_per_usdt > 0:
        _append_leg(
            out,
            rub=float(legs.transfer_rub_per_usdt),
            via_htx=legs.transfer_via_htx,
            kind="transfer",
            thbt_usdt=thbt_usdt,
            thbt_usdc=thbt_usdc,
            ask=ask,
        )
    return out or None


def _warn_if_silent(ctx, *, needle: str, text: str) -> None:
    if any(needle in w for w in ctx.warnings):
        return
    ctx.warnings.append(text)


def summary(ctx) -> Optional[list]:
    doc = ctx.unified_doc
    if doc is not None:
        from rates_primitives import read_bybit_usdc_usdt_ask, read_truemoney_thbt

        usdt_out, usdc_out, w_tm = read_truemoney_thbt(doc)
        ask, w_ask = read_bybit_usdc_usdt_ask(doc)
        legs = read_summary_p2p_rub_legs(doc)
        ctx.warnings.extend(w_tm)
        ctx.warnings.extend(w_ask)
        ctx.warnings.extend(legs.warnings)
        ask_raw: object = ask
    else:
        tm = fetch_thbt_quote_payload()
        spot = fetch_ask_payload()
        legs = fetch_summary_p2p_rub_legs_live()
        ctx.warnings.extend(list(tm.get("warnings") or []))
        ctx.warnings.extend(list(spot.get("warnings") or []))
        ctx.warnings.extend(legs.warnings)
        usdt_out = tm.get("usdt_amount_out")
        usdc_out = tm.get("usdc_amount_out")
        ask_raw = spot.get("ask_usdt_per_usdc")

    if _pos_int(usdt_out) is None:
        _warn_if_silent(ctx, needle="USDT→THBT", text="TrueMoney: нет котировки USDT→THBT")
    if _pos_int(usdc_out) is None:
        _warn_if_silent(ctx, needle="USDC→THBT", text="TrueMoney: нет котировки USDC→THBT")
    elif parse_ask(ask_raw) is None:
        _warn_if_silent(ctx, needle="USDCUSDT", text="Bybit: нет ask USDCUSDT")

    return quotes_from_legs(
        legs,
        usdt_amount_out=usdt_out,
        usdc_amount_out=usdc_out,
        ask_usdt_per_usdc=ask_raw,
    )
