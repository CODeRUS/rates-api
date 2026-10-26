# -*- coding: utf-8 -*-
"""Вернадский (profikassa.ru): наличные USD/EUR/CNY, продажа с сайта."""
from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from rates_sources import FetchContext, SourceQuote

import rates_unified_cache as ucc
from rates_categories import SourceCategory

from sources.tilda_msk_cash import (
    chatcash_payload_rows,
    fetch_apps_script_rates,
    fetch_page_html,
    sell_rows_from_rate_items,
)

SOURCE_ID = "vernadsky_msk"
EMOJI = "•"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_URL = "https://profikassa.ru/"
_DISPLAY_NAME = "Вернадский"
_CITY = "Москва"
_CHATCASH_TTL_SEC = 30 * 60
_LOC = "v"
_API_RE = re.compile(r"""var\s+API\s*=\s*['"]([^'"]+)['"]""")
_MISSING = "в API нет продажи usd3 / eur3 / cny"


def help_text() -> str:
    return (
        f"{_DISPLAY_NAME}: курсы продажи USD (старого образца), EUR (2002), CNY с {_URL} "
        "(API action=rates&loc=v: usd3 / eur3 / cny)."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def api_url_from_html(html: str) -> Optional[str]:
    match = _API_RE.search(html or "")
    if match is None:
        return None
    url = match.group(1).strip()
    return url or None


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
    api_url = api_url_from_html(html)
    if not api_url:
        ctx.warnings.append(f"{_DISPLAY_NAME}: на странице нет URL API курсов")
        return None
    try:
        payload = fetch_apps_script_rates(api_url, loc=_LOC)
    except Exception as e:
        ctx.warnings.append(f"{_DISPLAY_NAME}: не удалось загрузить курсы: {e}")
        return None
    sells = sell_rows_from_rate_items(payload)
    if not sells:
        ctx.warnings.append(f"{_DISPLAY_NAME}: {_MISSING}")
        return None
    rows = chatcash_payload_rows(
        source_id=SOURCE_ID,
        source_name=_DISPLAY_NAME,
        city=_CITY,
        rows=sells,
        message_unix=time.time(),
    )
    doc = ctx.unified_doc
    if isinstance(doc, dict):
        _sync_chatcash(doc, rows)
    return None
