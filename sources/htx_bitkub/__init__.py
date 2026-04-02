# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "htx_bitkub"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "HTX P2P USDT/RUB + Bitkub THB/USDT.\n"
        "  bitkub …  — тикер Bitkub (подкоманда, см. htx_bitkub bitkub --help)\n"
        "  иначе     — HTX OTC trade-market (htx_p2p_usdt_rub)"
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print(
            "\nПример: rates.py htx_bitkub --json\n"
            "         rates.py htx_bitkub bitkub --json",
            file=sys.stderr,
        )
        return 0
    if argv[0] == "bitkub":
        from ..bybit_bitkub.bitkub_usdt_thb import cli_main

        return cli_main(argv[1:])
    from .htx_p2p_usdt_rub import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from rates_primitives import read_bitkub_bid, read_htx_p2p

    from ..bybit_bitkub import bitkub_usdt_thb as bk
    from . import htx_p2p_usdt_rub as hx

    doc = ctx.unified_doc
    if doc is not None:
        thb_usdt, w_bk = read_bitkub_bid(doc)
        cash_p, tr_p, w_hx = read_htx_p2p(doc)
        ctx.warnings.extend(w_bk)
        ctx.warnings.extend(w_hx)
        if thb_usdt is not None and thb_usdt > 0:
            out: List[SourceQuote] = []
            if cash_p is not None and cash_p > 0:
                out.append(
                    SourceQuote(
                        cash_p / thb_usdt,
                        "HTX P2P (наличные) → Bitkub",
                        merge_key="htx_cash",
                    )
                )
            else:
                ctx.warnings.append(
                    "HTX: нет объявлений с наличными под фильтры (100 USDT, minTradeLimit≥100·price)"
                )
            if tr_p is not None and tr_p > 0:
                out.append(
                    SourceQuote(
                        tr_p / thb_usdt,
                        "HTX P2P (пеервод) → Bitkub",
                        merge_key="htx_no_cash",
                    )
                )
            else:
                ctx.warnings.append(
                    "HTX: нет объявлений без наличных под фильтры (100 USDT, minTradeLimit≥100·price)"
                )
            return out or None
        ctx.warnings.append("Bitkub: нет highestBid для USDT")
        return None

    try:
        ia, ib = hx.fetch_best_cash_and_non_cash_offers(max_pages=30)
    except RuntimeError as e:
        ctx.warnings.append(f"HTX OTC: {e}")
        return None
    try:
        tk = bk.fetch_ticker()
    except RuntimeError as e:
        ctx.warnings.append(f"Bitkub: {e}")
        return None
    thb_usdt = float(tk.get("highestBid") or 0)
    if thb_usdt <= 0:
        ctx.warnings.append("Bitkub: нет highestBid для USDT")
        return None

    out = []
    if ia:
        out.append(
            SourceQuote(
                float(ia["price"]) / thb_usdt,
                "HTX P2P (наличные) → Bitkub",
                merge_key="htx_cash",
            )
        )
    else:
        ctx.warnings.append(
            "HTX: нет объявлений с наличными под фильтры (100 USDT, minTradeLimit≥100·price)"
        )
    if ib:
        out.append(
            SourceQuote(
                float(ib["price"]) / thb_usdt,
                "HTX P2P (пеервод) → Bitkub",
                merge_key="htx_no_cash",
            )
        )
    else:
        ctx.warnings.append(
            "HTX: нет объявлений без наличных под фильтры (100 USDT, minTradeLimit≥100·price)"
        )
    return out or None
