# -*- coding: utf-8 -*-
"""Вернадский (profikassa.ru): наличные USD/EUR/CNY, продажа с сайта."""
from __future__ import annotations

import time
from typing import List, Optional

import rates_unified_cache as ucc
from rates_categories import SourceCategory
from rates_sources import FetchContext, SourceQuote

from sources.tilda_msk_cash import (
    cash_sell_rows_from_html,
    chatcash_payload_rows,
    fetch_page_html,
)

SOURCE_ID = "vernadsky_msk"
EMOJI = "•"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_URL = "https://profikassa.ru/"
_DISPLAY_NAME = "Вернадский"
_CITY = "Москва"
_CHATCASH_TTL_SEC = 30 * 60


def help_text() -> str:
    return (
        f"{_DISPLAY_NAME}: курсы продажи USD (1996–2006), EUR (2002), CNY с {_URL} "
        "(Tilda, классы rate-*-sell)."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _sync_chatcash(doc: dict, rows: list) -> None:
    if not rows:
        return
    ucc.l1_set(
        doc,
        f"chatcash:{SOURCE_ID}",
        rows,
        ttl_sec=_CHATCASH_TTL_SEC,
    )


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    try:
        html = fetch_page_html(_URL)
    except Exception as e:
        ctx.warnings.append(f"{_DISPLAY_NAME}: не удалось загрузить {_URL}: {e}")
        return None
    sells = cash_sell_rows_from_html(html)
    if not sells:
        ctx.warnings.append(
            f"{_DISPLAY_NAME}: на странице нет rate-usd-old-sell / rate-eur2002-sell / rate-cny-sell"
        )
        return None
    payload = chatcash_payload_rows(
        source_id=SOURCE_ID,
        source_name=_DISPLAY_NAME,
        city=_CITY,
        rows=sells,
        message_unix=time.time(),
    )
    doc = ctx.unified_doc
    if isinstance(doc, dict):
        _sync_chatcash(doc, payload)
    return None
