# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "bybit_binanceth"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "Bybit P2P USDT/RUB + Binance TH spot USDT/THB (bid).\n"
        "  binance_th …  — bookTicker (см. bybit_binanceth binance_th --help)\n"
        "  иначе         — Bybit P2P CLI (bybit_p2p_usdt_rub)"
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print(
            "\nПример: rates.py bybit_binanceth binance_th --json",
            file=sys.stderr,
        )
        return 0
    if argv[0] == "binance_th":
        from ..binance_th.usdt_thb_book import cli_main

        return cli_main(argv[1:])
    from ..bybit_bitkub.bybit_p2p_usdt_rub import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from ..binance_th.usdt_thb_book import fetch_bid_thb_per_usdt
    from ..bybit_bitkub import bybit_p2p_usdt_rub as bp

    items = bp.fetch_all_online_items(size=20, verification_filter=0)
    items = bp.filter_by_target_usdt(items, target_usdt=bp.DEFAULT_TARGET_USDT)
    a = bp.filter_cash_deposit_to_bank(items, 99.0)
    b = bp.filter_bank_transfer_no_cash(items, 99.0)
    ia = bp.min_by_price(a)
    ib = bp.min_by_price(b)
    try:
        thb_usdt = fetch_bid_thb_per_usdt()
    except RuntimeError as e:
        ctx.warnings.append(f"Binance TH: {e}")
        return None

    out: List[SourceQuote] = []
    if ia:
        out.append(
            SourceQuote(
                float(ia["price"]) / thb_usdt,
                "Bybit P2P (cash) → Binance TH",
                merge_key="bybit_cash",
            )
        )
    else:
        ctx.warnings.append(
            "Bybit: нет объявлений Cash Deposit (18) с completion≥99 "
            "(100 USDT, minAmount≥100·price)"
        )
    if ib:
        out.append(
            SourceQuote(
                float(ib["price"]) / thb_usdt,
                "Bybit P2P (перевод) → Binance TH",
                merge_key="bybit_transfer",
            )
        )
    else:
        ctx.warnings.append(
            "Bybit: нет объявлений только перевод (14, без 18) с completion≥99 "
            "(100 USDT, minAmount≥100·price)"
        )
    return out or None
