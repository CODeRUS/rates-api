# -*- coding: utf-8 -*-
"""
Курсы «Продажа» обменников на Tilda (profikassa.ru, vernadka-kassa.ru).

Живые страницы больше не содержат ``rate-*-sell``: виджет читает Apps Script
``action=rates``. HTML-парсер оставлен для старых страниц и тестов.
Для сводки наличных в Москве: USD старого образца (usd3), EUR 2002 (eur3), CNY.
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from rates_http import urlopen_retriable

_USER_AGENT = "rates-api/tilda-msk-cash/1.0 (python)"

# (currency, category, css class suffix for sell rate)
_CASH_SELL_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("USD", "cash_usd", "rate-usd-old-sell"),
    ("EUR", "cash_eur", "rate-eur2002-sell"),
    ("CNY", "cash_cny", "rate-cny-sell"),
)

# id в ответе Apps Script. usd3 — старый USD (бывший rate-usd-old-sell).
API_CASH_SELL_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("USD", "cash_usd", "usd3"),
    ("EUR", "cash_eur", "eur3"),
    ("CNY", "cash_cny", "cny"),
)

_APPS_SCRIPT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_RATE_CLASS_INLINE_RE = re.compile(
    r"class=['\"][^'\"]*\b(rate-[a-z0-9-]+)\b[^'\"]*['\"][^>]*>([^<]+)<",
    re.IGNORECASE,
)
_RATE_TN_ELEM_RE = re.compile(
    r"tn-elem[^>]*\b(rate-[a-z0-9-]+)\b[^>]*>[\s\S]{0,500}?(?:tn-atom[^>]*>)?(\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CashSellRow:
    currency: str
    category: str
    rate: float


def fetch_page_html(url: str, *, timeout: float = 25.0) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": _USER_AGENT,
        },
    )
    with urlopen_retriable(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def _parse_rate_value(raw: str) -> Optional[float]:
    s = (raw or "").strip().replace(",", ".")
    if not re.match(r"^\d", s):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v > 0 else None


def parse_tilda_sell_rates(html: str) -> Dict[str, float]:
    """
    Все ``rate-*-sell`` / ``rate-*-buy`` с страницы Tilda.

    Сначала таблица на странице (``tn-elem`` + ``tn-atom``) — актуальные курсы.
    Блок ``.rates-data`` в калькуляторе может отставать; подставляем только если
    класса ещё нет в результате.
    """
    rates: Dict[str, float] = {}
    for m in _RATE_TN_ELEM_RE.finditer(html or ""):
        cls, raw = m.group(1), m.group(2)
        v = _parse_rate_value(raw)
        if v is not None:
            rates[cls] = v
    for m in _RATE_CLASS_INLINE_RE.finditer(html or ""):
        cls, raw = m.group(1), m.group(2)
        if cls in rates:
            continue
        v = _parse_rate_value(raw)
        if v is not None:
            rates[cls] = v
    return rates


def cash_sell_rows_from_html(html: str) -> List[CashSellRow]:
    parsed = parse_tilda_sell_rates(html)
    out: List[CashSellRow] = []
    for currency, category, cls in _CASH_SELL_SPECS:
        v = parsed.get(cls)
        if v is None or v <= 0:
            continue
        out.append(CashSellRow(currency=currency, category=category, rate=v))
    return out


def sell_rows_from_rate_items(
    data: Any,
    specs: Sequence[Tuple[str, str, str]] = API_CASH_SELL_SPECS,
) -> List[CashSellRow]:
    """Продажа из ``{ok, rates:[{id, sell, on}]}``. Строки с ``on is False`` пропускаются."""
    if not isinstance(data, dict) or not data.get("ok"):
        return []
    rates = data.get("rates")
    if not isinstance(rates, list):
        return []
    by_id: Dict[str, float] = {}
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
    for currency, category, rate_id in specs:
        sell = by_id.get(rate_id)
        if sell is None:
            continue
        out.append(CashSellRow(currency=currency, category=category, rate=sell))
    return out


def fetch_apps_script_rates(
    api_url: str,
    *,
    loc: Optional[str] = None,
    timeout: float = 25.0,
) -> Any:
    params: Dict[str, Any] = {"action": "rates", "t": int(time.time() * 1000)}
    if loc:
        params["loc"] = loc
    query = urllib.parse.urlencode(params)
    sep = "&" if "?" in api_url else "?"
    req = urllib.request.Request(
        api_url + sep + query,
        headers={"Accept": "application/json", "User-Agent": _APPS_SCRIPT_UA},
    )
    with urlopen_retriable(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"не JSON: {raw[:120]!r}") from exc


def chatcash_payload_rows(
    *,
    source_id: str,
    source_name: str,
    city: str,
    rows: List[CashSellRow],
    message_unix: Optional[float] = None,
) -> List[dict]:
    ts = float(message_unix if message_unix is not None else time.time())
    return [
        {
            "source_id": source_id,
            "source_name": source_name,
            "currency": r.currency,
            "category": r.category,
            "rate": r.rate,
            "message_id": 0,
            "message_unix": ts,
            "chat": "",
            "city": city,
        }
        for r in rows
    ]
