# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "korona"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "KoronaPay тарифы (крупная и малая сумма)."


def command(argv: list[str]) -> int:
    if not argv or "--help" in argv or "-h" in argv:
        print(help_text())
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import koronapay_tariffs as kp

    out: List[SourceQuote] = []
    large = ctx.korona_large_thb
    lbl_large = f"Korona (от {fmt_money_ru(large)} THB)"
    try:
        rows_kp = kp.fetch_tariffs(receiving_amount_satang=kp.thb_to_satang(large))
        row = rows_kp[0]
        rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
        thb = kp.satang_to_thb(int(row["receivingAmount"]))
        if thb > 0:
            out.append(SourceQuote(rub / thb, lbl_large))
    except Exception as e:
        ctx.warnings.append(f"Korona {lbl_large}: {e}")

    small = ctx.korona_small_rub
    try:
        rows_kp = kp.fetch_tariffs(sending_amount_kopecks=kp.rub_to_kopecks(small))
        row = rows_kp[0]
        rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
        thb = kp.satang_to_thb(int(row["receivingAmount"]))
        if thb > 0:
            out.append(SourceQuote(rub / thb, "Korona (малые суммы)"))
    except Exception as e:
        ctx.warnings.append(f"Korona (малые суммы): {e}")

    return out or None
