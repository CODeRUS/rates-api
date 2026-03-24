# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "forex"
EMOJI = "📈"
IS_BASELINE = True
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "Курс THB→RUB для сводки — XE midmarket.\n"
        "CLI: подкоманда «xe» — клиент Xe.com; «er» — ExchangeRate-API (open.er-api.com).\n"
        "Полные опции: forex xe --help   и   forex er --help"
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print("\n--- XE (forex_xe_api): подкоманды midmarket | convert ---")
        from .forex_xe_api import build_arg_parser

        build_arg_parser().print_help()
        print("\n--- ER (forex_er_api): latest | convert | rate | matrix ---")
        from .forex_er_api import build_arg_parser as build_er_parser

        build_er_parser().print_help()
        return 0
    if argv[0] == "er":
        from .forex_er_api import cli_main as er_main

        return er_main(argv[1:])
    if argv[0] == "xe":
        from .forex_xe_api import cli_main as xe_main

        return xe_main(argv[1:])
    from .forex_xe_api import cli_main as xe_main

    return xe_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import forex_xe_api as xe

    conv = xe.midmarket_convert("THB", "RUB", 1.0)
    return [SourceQuote(float(conv["result"]), "Forex")]
