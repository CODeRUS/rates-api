# -*- coding: utf-8 -*-
"""Вернадский (profikassa.ru): наличные USD/EUR/CNY, продажа с сайта."""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any, List, Optional

if TYPE_CHECKING:
    from rates_sources import FetchContext, SourceQuote

import rates_unified_cache as ucc
from rates_categories import SourceCategory
from rates_http import urlopen_retriable

from sources.tilda_msk_cash import CashSellRow, chatcash_payload_rows, fetch_page_html

SOURCE_ID = "vernadsky_msk"
EMOJI = "•"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_URL = "https://profikassa.ru/"
_DISPLAY_NAME = "Вернадский"
_CITY = "Москва"
_CHATCASH_TTL_SEC = 30 * 60
_LOC = "v"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_API_RE = re.compile(r"""var\s+API\s*=\s*['"]([^'"]+)['"]""")
# usd3 — старого образца (бывший rate-usd-old-sell), eur3 — серия 2002, cny — юань.
_SELL_SPECS = (
    ("USD", "cash_usd", "usd3"),
    ("EUR", "cash_eur", "eur3"),
    ("CNY", "cash_cny", "cny"),
)


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


def sell_rows_from_payload(data: Any) -> List[CashSellRow]:
    if not isinstance(data, dict) or not data.get("ok"):
        return []
    rates = data.get("rates")
    if not isinstance(rates, list):
        return []
    by_id = {}
    for item in rates:
        if not isinstance(item, dict) or item.get("on") is False:
            continue
        rate_id = item.get("id")
        try:
            sell = float(item.get("sell"))
        except (TypeError, ValueError):
            continue
        if isinstance(rate_id, str) and sell > 0:
            by_id[rate_id] = sell
    out: List[CashSellRow] = []
    for currency, category, rate_id in _SELL_SPECS:
        sell = by_id.get(rate_id)
        if sell is None:
            continue
        out.append(CashSellRow(currency=currency, category=category, rate=sell))
    return out


def fetch_rates_payload(api_url: str, *, timeout: float = 25.0) -> Any:
    query = urllib.parse.urlencode({"action": "rates", "loc": _LOC, "t": int(time.time() * 1000)})
    sep = "&" if "?" in api_url else "?"
    req = urllib.request.Request(
        api_url + sep + query,
        headers={"Accept": "application/json", "User-Agent": _UA},
    )
    with urlopen_retriable(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"не JSON: {raw[:120]!r}") from exc


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
        payload = fetch_rates_payload(api_url)
    except Exception as e:
        ctx.warnings.append(f"{_DISPLAY_NAME}: не удалось загрузить курсы: {e}")
        return None
    sells = sell_rows_from_payload(payload)
    if not sells:
        ctx.warnings.append(f"{_DISPLAY_NAME}: в API нет продажи usd3 / eur3 / cny")
        return None
    payload_rows = chatcash_payload_rows(
        source_id=SOURCE_ID,
        source_name=_DISPLAY_NAME,
        city=_CITY,
        rows=sells,
        message_unix=time.time(),
    )
    doc = ctx.unified_doc
    if isinstance(doc, dict):
        _sync_chatcash(doc, payload_rows)
    return None
