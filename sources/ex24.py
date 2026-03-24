# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "ex24"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "Ex24.pro курс RUB→THB с сайта."


def command(argv: list[str]) -> int:
    if not argv or "--help" in argv or "-h" in argv:
        print(help_text())
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import ex24_rub_thb as e24

    rr = e24.try_fetch_real_rate_rub_thb() or e24.DEFAULT_REAL_RATE
    rub_best = float(e24.RUB_MIN_FOR_ZERO_MARKUP)
    r_ex = e24.customer_rate_rub_per_thb(rub_best, rr)
    return [SourceQuote(r_ex, "Ex24.pro", note=f"от {fmt_money_ru(rub_best)} RUB")]
