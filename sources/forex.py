# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "forex"
EMOJI = "📈"
IS_BASELINE = True
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "Курс THB→RUB по XE midmarket (база для %% в сводке)."


def command(argv: list[str]) -> int:
    if not argv or "--help" in argv or "-h" in argv:
        print(help_text())
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import forex_xe_api as xe

    conv = xe.midmarket_convert("THB", "RUB", 1.0)
    return [SourceQuote(float(conv["result"]), "Forex")]
