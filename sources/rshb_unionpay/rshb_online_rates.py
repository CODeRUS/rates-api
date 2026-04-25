#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Курсы РСХБ для операций с картами **в сети банка** (JSON API).

URL: https://www.rshb.ru/api/v1/rates

Ответ — массив снимков вида ``[[{"currencyPair":"CNY/RUB_TOD","buyRate":...,
"sellRate":...,"lastUpdatedAt":"..."}, ...]]``. Для сценария «юаневая карта /
приложение» используется **sellRate** по паре **CNY/RUB** (суффикс ``_TOD``
отбрасывается при разборе).

Исторические даты: endpoint отдаёт только **текущий** снимок; параметры ``?date=``
не поддерживаются. При вызове :func:`cny_rur_sell` с ``on=`` дата должна совпадать
с календарной датой ``lastUpdatedAt`` снимка (иначе :class:`KeyError`).
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable

from . import rshb_offline_rates as _off

RSHB_RATES_API_URL = "https://www.rshb.ru/api/v1/rates"

# Совместимость со старыми именами (те же JSON-функции).
RSHB_ONLINE_URL = RSHB_RATES_API_URL


def _normalize_pair_label(currency_pair: str) -> str:
    s = currency_pair.strip().upper().replace(" ", "")
    if s.endswith("_TOD"):
        s = s[:-4]
    return s


def _snapshot_date(raw_rows: List[Dict[str, Any]]) -> date:
    """Календарная дата снимка по lastUpdatedAt строки CNY/RUB, иначе первой строки."""
    ts: Optional[str] = None
    for r in raw_rows:
        cp = str(r.get("currencyPair", ""))
        u = cp.upper().replace(" ", "")
        if "CNY" in u and ("RUB" in u or "RUR" in u):
            ts = r.get("lastUpdatedAt")  # type: ignore[assignment]
            break
    if ts is None and raw_rows:
        ts = raw_rows[0].get("lastUpdatedAt")  # type: ignore[assignment]
    if not ts:
        raise ValueError("В ответе API v1/rates нет lastUpdatedAt")
    if isinstance(ts, str):
        s = ts.replace("Z", "+00:00")
        return datetime.fromisoformat(s).date()
    raise ValueError("lastUpdatedAt должен быть строкой ISO-8601")


def _flatten_top_level(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list) and data and isinstance(data[0], list):
        inner = data[0]
    elif isinstance(data, list):
        inner = data
    else:
        raise ValueError("Ответ API v1/rates: ожидался массив котировок")
    out: List[Dict[str, Any]] = []
    for item in inner:
        if isinstance(item, dict):
            out.append(item)
    return out


def fetch_rates_json(*, timeout: float = 60.0, url: Optional[str] = None) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url or RSHB_RATES_API_URL,
        headers={
            "User-Agent": _off.USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        },
    )
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", errors="replace")


def parse_rates_json(raw: str) -> Dict[date, List[_off.PairQuote]]:
    """Разбирает JSON ``/api/v1/rates`` в ``{дата_снимка: [PairQuote, ...]}``."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Не удалось распарсить JSON API v1/rates: {e}") from e
    raw_rows = _flatten_top_level(data)
    quotes: List[_off.PairQuote] = []
    for row in raw_rows:
        cp = row.get("currencyPair")
        if not isinstance(cp, str):
            continue
        try:
            buy = Decimal(str(row["buyRate"]))
            sell = Decimal(str(row["sellRate"]))
        except (KeyError, TypeError, ValueError):
            continue
        label = _normalize_pair_label(cp)
        quotes.append(_off.PairQuote(label, buy, sell))
    if not quotes:
        return {}
    snap = _snapshot_date(raw_rows)
    return {snap: quotes}


def parse_online_html(html: str) -> Dict[date, List[_off.PairQuote]]:
    """Устаревшее имя: то же, что :func:`parse_rates_json`."""
    return parse_rates_json(html)


fetch_online_page = fetch_rates_json


def get_table_for_date(
    html: Optional[str] = None,
    *,
    on: Optional[date] = None,
    timeout: float = 60.0,
) -> List[_off.PairQuote]:
    raw = html if html is not None else fetch_rates_json(timeout=timeout)
    tables = parse_rates_json(raw)
    if not tables:
        raise ValueError("Не удалось распарсить ответ API v1/rates")
    if on is not None:
        if on not in tables:
            raise KeyError(
                f"Нет снимка API на {on.isoformat()} "
                f"(есть только: {sorted(tables.keys(), reverse=True)})"
            )
        return tables[on]
    latest = max(tables.keys())
    return tables[latest]


def cny_rur_sell(*, on: Optional[date] = None, html: Optional[str] = None) -> Decimal:
    """CNY/RUR, колонка ПРОДАЖА — для сценария «юаневая карта / операции в сети банка»."""
    raw = html if html is not None else fetch_rates_json()
    tables = parse_rates_json(raw)
    if not tables:
        raise ValueError("Не удалось распарсить ответ API v1/rates")

    def _pick_direct(rows: List[_off.PairQuote]) -> Optional[Decimal]:
        for q in rows:
            key = q.pair.replace(" ", "").upper()
            if key in ("CNY/RUR", "CNY/RUB"):
                return q.sell
        return None

    snap = max(tables.keys())
    rows = tables[snap]

    if on is not None and on != snap:
        raise KeyError(
            f"Нет снимка API на {on.isoformat()} (снимок только за {snap.isoformat()}; "
            "исторические даты endpoint не поддерживает)"
        )

    got = _pick_direct(rows)
    if got is not None:
        return got
    raise KeyError("Пара CNY/RUR не найдена в ответе API v1/rates")


def cli_main(argv=None) -> int:
    import sys

    if argv:
        print(
            "rshb_online_rates: без аргументов; печать котировок из API v1/rates.",
            file=sys.stderr,
        )
        return 2
    raw = fetch_rates_json()
    tabs = parse_rates_json(raw)
    if not tabs:
        print("Пустой или нераспознанный ответ API.", file=sys.stderr)
        return 1
    print("Дата снимка:", max(tabs.keys()))
    t = get_table_for_date(raw)
    for q in t:
        print(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
