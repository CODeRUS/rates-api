# -*- coding: utf-8 -*-
"""Плагин Payscan: курс RUB/THB с zap_ok_rate (plain text)."""
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "payscan"
EMOJI = "📲"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "Payscan zap ok THB→RUB (GET .php?d=THB, ответ — число ₽ за 1 THB). "
        "CLI: без аргументов — курс. Переменная PAYSCAN_THB_URL."
    )


def command(argv: list[str]) -> int:
    from .payscan_rub_thb import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import payscan_rub_thb as ps

    r = ps.fetch_rub_per_thb()
    return [SourceQuote(r, "Payscan")]


if __name__ == "__main__":
    raise SystemExit(command(sys.argv[1:]))
