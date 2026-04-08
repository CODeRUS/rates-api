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

from rates_http import urlopen_retriable
from decimal import Decimal
from typing import Dict, List, Optional

from . import rshb_offline_rates as _off

RSHB_ONLINE_URL = "https://old.rshb.ru/natural/cards/rates/rates_online/"
RSHB_ONLINE_ARCHIVE_URL = (
    "https://old.rshb.ru/natural/cards/rates/rates_online/?date_from={date_from}&date_to={date_to}"
)


def _fmt_ru_date(d: date) -> str:
    return f"{d.day}.{d.month:02d}.{d.year:04d}"


def fetch_online_page(*, timeout: float = 60.0, url: Optional[str] = None) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url or RSHB_ONLINE_URL,
        headers={
            "User-Agent": _off.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
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
    raw = html if html is not None else fetch_online_page()
    tables = parse_online_html(raw)
    if not tables:
        raise ValueError("Не удалось распарсить таблицы rates_online")

    def _pick(rows: List[_off.PairQuote]) -> Optional[Decimal]:
        for q in rows:
            if q.pair.replace(" ", "").upper() in ("CNY/RUR", "CNY/RUB"):
                return q.sell
        return None

    if on is not None:
        rows = tables.get(on)
        if rows is None:
            raise KeyError(
                f"Нет данных на {on.isoformat()} (есть: {sorted(tables.keys(), reverse=True)[:5]}…)"
            )
        got = _pick(rows)
        if got is not None:
            return got
        raise KeyError(f"Пара CNY/RUR не найдена на rates_online за {on.isoformat()}")

    def _find_latest_with_pair(tabs: Dict[date, List[_off.PairQuote]]) -> Optional[Decimal]:
        for d in sorted(tabs.keys(), reverse=True):
            got = _pick(tabs[d])
            if got is not None:
                return got
        return None

    # Без явной даты: сначала текущая страница, затем архивный диапазон.
    got = _find_latest_with_pair(tables)
    if got is not None:
        return got

    latest = max(tables.keys())
    date_to = latest
    date_from = date.fromordinal(max(1, date_to.toordinal() - 40))
    arch_url = RSHB_ONLINE_ARCHIVE_URL.format(
        date_from=_fmt_ru_date(date_from),
        date_to=_fmt_ru_date(date_to),
    )
    raw_arch = fetch_online_page(url=arch_url)
    tabs_arch = parse_online_html(raw_arch)
    if on is not None:
        rows_arch = tabs_arch.get(on)
        if rows_arch is not None:
            got_on = _pick(rows_arch)
            if got_on is not None:
                return got_on
        raise KeyError(f"Пара CNY/RUR не найдена на rates_online за {on.isoformat()}")
    got_arch = _find_latest_with_pair(tabs_arch)
    if got_arch is not None:
        return got_arch
    raise KeyError("Пара CNY/RUR не найдена на rates_online (ни на одной доступной дате)")


def cli_main(argv=None) -> int:
    import sys

    if argv:
        print("rshb_online_rates: без аргументов; печать последней таблицы rates_online.", file=sys.stderr)
        return 2
    raw = fetch_online_page()
    tabs = parse_online_html(raw)
    print("Даты (пример):", sorted(tabs.keys(), reverse=True)[:5])
    t = get_table_for_date(raw)
    for q in t:
        print(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
