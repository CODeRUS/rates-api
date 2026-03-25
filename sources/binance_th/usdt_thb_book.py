#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Котировка USDT/THB на Binance Thailand (спот), лучший **bid** — THB за 1 USDT при продаже USDT в стакан.

Публичный GET (без ключа)::

    GET https://api.binance.th/api/v1/ticker/bookTicker?symbol=USDTTHB

Документация: https://www.binance.th/api-docs/en/
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict

BASE_URL = "https://api.binance.th"
SYMBOL = "USDTTHB"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


def book_ticker_url(*, symbol: str = SYMBOL) -> str:
    q = urllib.parse.urlencode({"symbol": symbol})
    return f"{BASE_URL}/api/v1/ticker/bookTicker?{q}"


def fetch_book_ticker(*, symbol: str = SYMBOL, timeout: float = 30.0) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(book_ticker_url(symbol=symbol), headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            data = json.loads(r.read().decode(r.headers.get_content_charset() or "utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} при запросе bookTicker Binance TH") from e
    except urllib.error.URLError as e:
        raise RuntimeError(str(e)) from e
    if not isinstance(data, dict):
        raise RuntimeError("Binance TH: неверный формат ответа")
    return data


def fetch_bid_thb_per_usdt(*, symbol: str = SYMBOL, timeout: float = 30.0) -> float:
    """Лучший bid: сколько THB за 1 USDT (продажа USDT в стакан)."""
    t = fetch_book_ticker(symbol=symbol, timeout=timeout)
    try:
        bid = float(t.get("bidPrice") or 0)
    except (TypeError, ValueError) as e:
        raise RuntimeError("Binance TH: нет числового bidPrice") from e
    if bid <= 0:
        raise RuntimeError("Binance TH: bidPrice <= 0")
    return bid


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Binance TH: bookTicker USDTTHB (bid/ask)")
    ap.add_argument("--json", action="store_true", help="Сырой JSON bookTicker")
    return ap


def cli_main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        t = fetch_book_ticker()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(t, ensure_ascii=False, indent=2))
        return 0
    try:
        bid = float(t.get("bidPrice") or 0)
        ask = float(t.get("askPrice") or 0)
    except (TypeError, ValueError):
        print("Нет bid/ask", file=sys.stderr)
        return 1
    print(f"Пара: {SYMBOL} (https://www.binance.th/en/trade/USDT_THB)")
    print(f"  bidPrice (продажа USDT в стакан): {bid} THB за 1 USDT")
    print(f"  askPrice (покупка USDT):         {ask} THB за 1 USDT")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
