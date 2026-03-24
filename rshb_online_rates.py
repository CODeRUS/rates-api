#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Курсы РСХБ для операций с картами **в сети банка** (HTML).

URL: https://old.rshb.ru/natural/cards/rates/rates_online/

На странице несколько снимков с **одной календарной датой** (разное время);
берём **первый** блок после каждой уникальной даты при агрегации — это самый
свежий снимок вверху страницы (см. ``duplicate_date_policy="first"`` в
:func:`rshb_offline_rates.parse_offline_html`).
"""

from __future__ import annotations

import ssl
import urllib.request
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional

import rshb_offline_rates as _off

RSHB_ONLINE_URL = "https://old.rshb.ru/natural/cards/rates/rates_online/"


def fetch_online_page(*, timeout: float = 60.0) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        RSHB_ONLINE_URL,
        headers={
            "User-Agent": _off.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_online_html(html: str) -> Dict[date, List[_off.PairQuote]]:
    return _off.parse_offline_html(html, duplicate_date_policy="first")


def get_table_for_date(
    html: Optional[str] = None,
    *,
    on: Optional[date] = None,
    timeout: float = 60.0,
) -> List[_off.PairQuote]:
    raw = html if html is not None else fetch_online_page(timeout=timeout)
    tables = parse_online_html(raw)
    if not tables:
        raise ValueError("Не удалось распарсить таблицы rates_online")
    if on is not None:
        if on not in tables:
            raise KeyError(
                f"Нет данных на {on.isoformat()} (есть: {sorted(tables.keys(), reverse=True)[:5]}…)"
            )
        return tables[on]
    latest = max(tables.keys())
    return tables[latest]


def cny_rur_sell(*, on: Optional[date] = None, html: Optional[str] = None) -> Decimal:
    """CNY/RUR, колонка ПРОДАЖА — для сценария «юаневая карта / операции в сети банка»."""
    rows = get_table_for_date(html, on=on)
    for q in rows:
        if q.pair.replace(" ", "").upper() in ("CNY/RUR", "CNY/RUB"):
            return q.sell
    raise KeyError("Пара CNY/RUR не найдена на rates_online")
