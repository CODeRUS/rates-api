# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import time
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

logger = logging.getLogger(__name__)

SOURCE_ID = "askmoney"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "askmoney.pro калькулятор. Полные опции: askmoney --help"


def command(argv: list[str]) -> int:
    from .askmoney_rub_thb import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import askmoney_rub_thb as am

    t0 = time.perf_counter()
    html = am.fetch_homepage_html()
    t1 = time.perf_counter()
    params = am.parse_params_from_html(html)
    t2 = time.perf_counter()
    logger.info(
        "askmoney: после загрузки HTML %.2fs, разбор параметров %.2fs",
        t1 - t0,
        t2 - t1,
    )
    best_rub, _bthb, _brt = am.min_effective_rate_rub_per_thb(params)
    t3 = time.perf_counter()
    logger.info(
        "askmoney: min_effective_rate_rub_per_thb (rub_cap по умолчанию 50M) %.2fs",
        t3 - t2,
    )
    thb_at = am.rub_to_thb(best_rub, params)
    rt = am.effective_rate_rub_per_thb(best_rub, thb_at)
    if rt is None:
        return None
    return [SourceQuote(rt, "askmoney", note=f"от {fmt_money_ru(best_rub)} RUB")]
