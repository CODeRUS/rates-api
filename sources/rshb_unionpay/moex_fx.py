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
USER_AGENT = "moex-fx/1.0 (python)"


def _get(url: str, *, timeout: float = 20.0) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def cny_rub_tom(
    *,
    board_preference: str = "CETS",
    field: str = "LAST",
    timeout: float = 20.0,
) -> float:
    """
    Возвращает выбранное поле котировки CNYRUB_TOM.

    :param board_preference: предпочитаемая доска (``CETS`` / ``CNGD`` и т.д.).
    :param field: имя колонки из ISS (``LAST``, ``MARKETPRICE``, ``WAPRICE`` …).
    """
    data = _get(ISS_URL, timeout=timeout)
    md = data["marketdata"]
    cols: List[str] = md["columns"]
    rows = md["data"]
    bi = cols.index("BOARDID")
    fi = cols.index(field)
    for row in rows:
        if row[bi] == board_preference:
            val = row[fi]
            if val is None:
                break
            return float(val)
    # fallback: первый ряд с ненулевым LAST
    li = cols.index("LAST")
    for row in rows:
        if row[li] is not None:
            return float(row[li])
    raise RuntimeError("Не удалось извлечь курс CNY/RUB из ответа MOEX")


def cli_main(argv=None) -> int:
    if argv:
        print("moex_fx: без подкоманд; печатается CNY/RUB TOM.", file=sys.stderr)
        return 2
    print("CNY/RUB (CNYRUB_TOM LAST, CETS если есть):", cny_rub_tom())
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
