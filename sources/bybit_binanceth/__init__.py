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
    from rates_p2p_rub_summary import (
        cash_label,
        cash_merge_key,
        fetch_summary_p2p_rub_legs_live,
        read_summary_p2p_rub_legs,
        transfer_label,
        transfer_merge_key,
    )
    from rates_primitives import read_binance_th_bid

    from ..binance_th.usdt_thb_book import fetch_bid_thb_per_usdt

    doc = ctx.unified_doc
    if doc is not None:
        thb_usdt, w_bn = read_binance_th_bid(doc)
        legs = read_summary_p2p_rub_legs(doc)
        ctx.warnings.extend(w_bn)
        ctx.warnings.extend(legs.warnings)
        if thb_usdt is None or thb_usdt <= 0:
            if w_bn:
                return None
            ctx.warnings.append("Binance TH: нет bid для USDT/THB")
            return None
        out: List[SourceQuote] = []
        if legs.cash_rub_per_usdt is not None and legs.cash_rub_per_usdt > 0:
            out.append(
                SourceQuote(
                    legs.cash_rub_per_usdt / thb_usdt,
                    cash_label(via_htx=legs.cash_via_htx, dest="Binance TH"),
                    merge_key=cash_merge_key(legs.cash_via_htx),
                )
            )
        if legs.transfer_rub_per_usdt is not None and legs.transfer_rub_per_usdt > 0:
            out.append(
                SourceQuote(
                    legs.transfer_rub_per_usdt / thb_usdt,
                    transfer_label(via_htx=legs.transfer_via_htx, dest="Binance TH"),
                    merge_key=transfer_merge_key(legs.transfer_via_htx),
                )
            )
        return out or None

    legs = fetch_summary_p2p_rub_legs_live()
    ctx.warnings.extend(legs.warnings)
    try:
        thb_usdt = fetch_bid_thb_per_usdt()
    except RuntimeError as e:
        ctx.warnings.append(f"Binance TH: {e}")
        return None

    out = []
    if legs.cash_rub_per_usdt is not None and legs.cash_rub_per_usdt > 0:
        out.append(
            SourceQuote(
                legs.cash_rub_per_usdt / thb_usdt,
                cash_label(via_htx=legs.cash_via_htx, dest="Binance TH"),
                merge_key=cash_merge_key(legs.cash_via_htx),
            )
        )
    if legs.transfer_rub_per_usdt is not None and legs.transfer_rub_per_usdt > 0:
        out.append(
            SourceQuote(
                legs.transfer_rub_per_usdt / thb_usdt,
                transfer_label(via_htx=legs.transfer_via_htx, dest="Binance TH"),
                merge_key=transfer_merge_key(legs.transfer_via_htx),
            )
        )
    return out or None
