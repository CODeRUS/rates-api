#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Курс CNY/RUB с Московской биржи (MOEX ISS), инструмент CNYRUB_TOM.

Пример запроса (рынок валюта, режим CETS — расчёты T+1):
https://iss.moex.com/iss/engines/currency/markets/selt/securities/CNYRUB_TOM.json

Поле ``LAST`` (или ``MARKETPRICE``) — ориентир «биржевой» цены; для согласования с
калькуляторами банков часто берут последнюю сделку на доске CETS.
"""

from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable

ISS_URL = (
    "https://iss.moex.com/iss/engines/currency/markets/selt/securities/"
    "CNYRUB_TOM.json?iss.meta=off&iss.only=marketdata"
)
# Последняя завершённая сессия CETS (CLOSE/WAPRICE), когда в marketdata нет сделок (LAST = null).
HISTORY_CETS_URL = (
    "https://iss.moex.com/iss/history/engines/currency/markets/selt/boards/"
    "CETS/securities/CNYRUB_TOM.json?iss.meta=off&limit=1&sort_order=desc"
)
USER_AGENT = "moex-fx/1.0 (python)"
_JSON_ACCEPT = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def _get(url: str, *, timeout: float = 20.0) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=_JSON_ACCEPT)
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def _first_float_in_row(row: List[Any], cols: List[str], names: List[str]) -> Optional[float]:
    for name in names:
        if name not in cols:
            continue
        val = row[cols.index(name)]
        if val is not None:
            return float(val)
    return None


def _cets_close_from_history(*, timeout: float) -> float:
    data = _get(HISTORY_CETS_URL, timeout=timeout)
    hist = data.get("history") or {}
    cols: List[str] = list(hist.get("columns") or [])
    rows = hist.get("data") or []
    if not cols or not rows:
        raise RuntimeError("MOEX history: нет строк курса CNYRUB_TOM (CETS)")
    v = _first_float_in_row(rows[0], cols, ["CLOSE", "WAPRICE"])
    if v is None:
        raise RuntimeError("MOEX history: нет CLOSE/WAPRICE в последней строке")
    return v


def cny_rub_tom(
    *,
    board_preference: str = "CETS",
    field: str = "LAST",
    timeout: float = 20.0,
) -> float:
    """
    Возвращает выбранное поле котировки CNYRUB_TOM.

    Если сессия ещё не дала сделок (``LAST`` = null на всех досках), берётся
    ``CLOSE`` последней торговой сессии CETS из блока history ISS.

    :param board_preference: предпочитаемая доска (``CETS`` / ``CNGD`` и т.д.).
    :param field: имя колонки из ISS (``LAST``, ``MARKETPRICE``, ``WAPRICE`` …).
    """
    data = _get(ISS_URL, timeout=timeout)
    md = data["marketdata"]
    cols: List[str] = md["columns"]
    rows = md["data"]
    bi = cols.index("BOARDID")
    price_chain = [field, "LAST", "MARKETPRICE", "WAPRICE", "CLOSEPRICE"]
    for row in rows:
        if row[bi] == board_preference:
            v = _first_float_in_row(row, cols, price_chain)
            if v is not None:
                return v
            break
    if "LAST" in cols:
        li = cols.index("LAST")
        for row in rows:
            if row[li] is not None:
                return float(row[li])
    for row in rows:
        v = _first_float_in_row(row, cols, ["MARKETPRICE", "WAPRICE", "CLOSEPRICE"])
        if v is not None:
            return v
    return _cets_close_from_history(timeout=timeout)


def cli_main(argv=None) -> int:
    if argv:
        print("moex_fx: без подкоманд; печатается CNY/RUB TOM.", file=sys.stderr)
        return 2
    print("CNY/RUB (CNYRUB_TOM LAST, CETS если есть):", cny_rub_tom())
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
