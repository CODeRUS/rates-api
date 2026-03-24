# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "bybit_bitkub"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "Bybit P2P USDT/RUB + Bitkub THB/USDT.\n"
        "  bitkub …  — тикер Bitkub (подкоманда, см. bybit_bitkub bitkub --help)\n"
        "  иначе     — аргументы передаются в Bybit P2P CLI (bybit_p2p_usdt_rub)"
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print(
            "\nПример: rates_summary_thb_rub.py bybit_bitkub --json\n"
            "         rates_summary_thb_rub.py bybit_bitkub bitkub --json",
            file=sys.stderr,
        )
        return 0
    if argv[0] == "bitkub":
        from .bitkub_usdt_thb import cli_main

        return cli_main(argv[1:])
    from .bybit_p2p_usdt_rub import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import bitkub_usdt_thb as bk
    from . import bybit_p2p_usdt_rub as bp

    items = bp.fetch_all_online_items(size=20, verification_filter=0)
    a = bp.filter_cash_deposit_to_bank(items, 99.0)
    b = bp.filter_bank_transfer_no_cash(items, 99.0)
    ia = bp.min_by_price(a)
    ib = bp.min_by_price(b)
    tk = bk.fetch_ticker()
    thb_usdt = float(tk.get("highestBid") or 0)
    if thb_usdt <= 0:
        ctx.warnings.append("Bitkub: нет highestBid для USDT")
        return None

    out: List[SourceQuote] = []
    if ia:
        out.append(SourceQuote(float(ia["price"]) / thb_usdt, "Bybit P2P (cash) → Bitkub"))
    else:
        ctx.warnings.append("Bybit: нет объявлений Cash Deposit (18) с completion≥99")
    if ib:
        out.append(SourceQuote(float(ib["price"]) / thb_usdt, "Bybit P2P (перевод) → Bitkub"))
    else:
        ctx.warnings.append("Bybit: нет объявлений только перевод (14, без 18) с completion≥99")
    return out or None
