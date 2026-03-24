# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "ex24"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "ex24.pro RUB→THB. Полные опции: ex24 --help"


def command(argv: list[str]) -> int:
    from .ex24_rub_thb import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import ex24_rub_thb as e24

    rr = e24.try_fetch_real_rate_rub_thb() or e24.DEFAULT_REAL_RATE
    rub_best = float(e24.RUB_MIN_FOR_ZERO_MARKUP)
    r_ex = e24.customer_rate_rub_per_thb(rub_best, rr)
    return [
        SourceQuote(
            r_ex,
            "Ex24.pro",
            note=f"от {fmt_money_ru(rub_best)} RUB",
        )
    ]
