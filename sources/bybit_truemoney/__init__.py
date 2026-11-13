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
from rates_p2p_rub_summary import transfer_label

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
        "Bybit P2P перевод USDT/RUB + TrueMoney THBT (Baht Swap, Base).\n"
        "Наличные в сводку не входят. Пустой перевод Bybit заменяется переводом HTX.\n"
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


def _append_transfer(
    out: list,
    *,
    rub: float,
    via_htx: bool,
    thbt_usdt: Optional[Decimal],
    thbt_usdc: Optional[Decimal],
    ask: Optional[Decimal],
) -> None:
    from rates_sources import SourceQuote

    rub_d = Decimal(str(rub))
    if thbt_usdt is not None and thbt_usdt > 0:
        out.append(
            SourceQuote(
                float(rub_d / thbt_usdt),
                transfer_label(via_htx=via_htx, dest=_DEST_USDT),
            )
        )
    if thbt_usdc is not None and thbt_usdc > 0 and ask is not None and ask > 0:
        out.append(
            SourceQuote(
                float(rub_d * ask / thbt_usdc),
                transfer_label(via_htx=via_htx, dest=_DEST_USDC),
            )
        )


def quotes_from_transfer(
    transfer_rub: Optional[float],
    *,
    via_htx: bool = False,
    usdt_amount_out: object,
    usdc_amount_out: object,
    ask_usdt_per_usdc: object,
) -> Optional[list]:
    if transfer_rub is None or transfer_rub <= 0:
        return None
    usdt_raw = _pos_int(usdt_amount_out)
    usdc_raw = _pos_int(usdc_amount_out)
    thbt_usdt = thbt_per_token(usdt_raw) if usdt_raw is not None else None
    thbt_usdc = thbt_per_token(usdc_raw) if usdc_raw is not None else None
    out: list = []
    _append_transfer(
        out,
        rub=float(transfer_rub),
        via_htx=via_htx,
        thbt_usdt=thbt_usdt,
        thbt_usdc=thbt_usdc,
        ask=parse_ask(ask_usdt_per_usdc),
    )
    return out or None


def _price(offer: object) -> Optional[float]:
    if not isinstance(offer, dict):
        return None
    try:
        price = float(offer.get("price"))
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def _read_transfer(doc) -> tuple:
    """(RUB/USDT перевода, via_htx). Наличные не используются. Пустой Bybit → HTX."""
    from rates_primitives import read_bybit_p2p, read_htx_p2p

    _cash, transfer, _warnings = read_bybit_p2p(doc)
    if transfer is not None and transfer > 0:
        return float(transfer), False
    _htx_cash, htx_transfer, _htx_warnings = read_htx_p2p(doc)
    if htx_transfer is not None and htx_transfer > 0:
        return float(htx_transfer), True
    return None, False


def _fetch_transfer() -> tuple:
    from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp

    try:
        _cash, transfer = bp.fetch_best_cash_and_bank_transfer_items(
            size=20,
            verification_filter=0,
            target_usdt=bp.DEFAULT_TARGET_USDT,
            min_completion=99.0,
        )
    except RuntimeError:
        transfer = None
    price = _price(transfer)
    if price is not None:
        return price, False

    from sources.htx_bitkub import htx_p2p_usdt_rub as hx

    try:
        _cash, htx_transfer = hx.fetch_best_cash_and_non_cash_offers(max_pages=30)
    except RuntimeError:
        return None, False
    htx_price = _price(htx_transfer)
    if htx_price is None:
        return None, False
    return htx_price, True


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
        transfer_rub, via_htx = _read_transfer(doc)
        ctx.warnings.extend(w_tm)
        ctx.warnings.extend(w_ask)
        ask_raw: object = ask
    else:
        tm = fetch_thbt_quote_payload()
        spot = fetch_ask_payload()
        transfer_rub, via_htx = _fetch_transfer()
        ctx.warnings.extend(list(tm.get("warnings") or []))
        ctx.warnings.extend(list(spot.get("warnings") or []))
        usdt_out = tm.get("usdt_amount_out")
        usdc_out = tm.get("usdc_amount_out")
        ask_raw = spot.get("ask_usdt_per_usdc")

    if _pos_int(usdt_out) is None:
        _warn_if_silent(ctx, needle="USDT→THBT", text="TrueMoney: нет котировки USDT→THBT")
    if _pos_int(usdc_out) is None:
        _warn_if_silent(ctx, needle="USDC→THBT", text="TrueMoney: нет котировки USDC→THBT")
    elif parse_ask(ask_raw) is None:
        _warn_if_silent(ctx, needle="USDCUSDT", text="Bybit: нет ask USDCUSDT")

    return quotes_from_transfer(
        transfer_rub,
        via_htx=via_htx,
        usdt_amount_out=usdt_out,
        usdc_amount_out=usdc_out,
        ask_usdt_per_usdc=ask_raw,
    )
