# -*- coding: utf-8 -*-
"""
Парсер курсов «Продажа» с сайтов обменников на Tilda (profikassa.ru, vernadka-kassa.ru).

Курсы в HTML: элементы с классом ``rate-*-sell`` / ``rate-*-buy`` (см. ``getVal('rate-usd-old')`` в странице).
Для сводки наличных в Москве берём продажу серии USD 1996–2006, EUR 2002, CNY.
"""
from __future__ import annotations

import re
import ssl
import time
import urllib.request
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from rates_http import urlopen_retriable

_USER_AGENT = "rates-api/tilda-msk-cash/1.0 (python)"

# (currency, category, css class suffix for sell rate)
_CASH_SELL_SPECS: Tuple[Tuple[str, str, str], ...] = (
    ("USD", "cash_usd", "rate-usd-old-sell"),
    ("EUR", "cash_eur", "rate-eur2002-sell"),
    ("CNY", "cash_cny", "rate-cny-sell"),
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
