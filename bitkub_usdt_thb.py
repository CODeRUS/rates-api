#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Текущие котировки USDT/THB с Bitkub (как на странице market/USDT_THB).

Публичный эндпоинт (без ключа)::

    GET https://api.bitkub.com/api/market/ticker

Ответ — объект по символам рынка. Пара **THB_USDT**: база THB, котируемая USDT;
поле ``last`` — последняя сделка в **THB за 1 USDT**.

Продажа USDT за THB (отдать USDT, получить баты):
  • **highestBid** — лучшая цена покупателя (в стакан можно «продать по биду»).
  • **lowestAsk** — лучшая цена продавца (если покупаете USDT).

Нужен обычный браузерный ``User-Agent``, иначе возможен HTTP 403.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

TICKER_URL = "https://api.bitkub.com/api/market/ticker"
SYMBOL = "THB_USDT"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


def fetch_ticker(*, timeout: float = 30.0) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(TICKER_URL, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            data = json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} при запросе тикера") from e
    except urllib.error.URLError as e:
        raise RuntimeError(str(e)) from e
    if not isinstance(data, dict) or SYMBOL not in data:
        raise RuntimeError(f"В ответе нет ключа {SYMBOL!r}")
    t = data[SYMBOL]
    if not isinstance(t, dict):
        raise RuntimeError("Неверный формат тикера")
    return t


def _main() -> int:
    ap = argparse.ArgumentParser(description="Bitkub: курс THB_USDT (USDT/THB)")
    ap.add_argument("--json", action="store_true", help="Вывести сырой JSON тикера")
    args = ap.parse_args()
    try:
        t = fetch_ticker()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0
    last = t.get("last")
    bid = t.get("highestBid")
    ask = t.get("lowestAsk")
    print(f"Пара: {SYMBOL} (страница: https://www.bitkub.com/en/market/USDT_THB)")
    print(f"  last (последняя):     {last} THB за 1 USDT")
    print(f"  highestBid (продажа): {bid} THB за 1 USDT — ориентир при продаже USDT в стакан")
    print(f"  lowestAsk (покупка):  {ask} THB за 1 USDT")
    print(f"  high24hr / low24hr:   {t.get('high24hr')} / {t.get('low24hr')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
