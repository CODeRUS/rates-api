# -*- coding: utf-8 -*-
"""Bybit spot USDCUSDT: сколько USDT стоит купить 1 USDC (ask1Price)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any, Dict, Optional

from rates_http import urlopen_retriable

TICKER_URL = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=USDCUSDT"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}


def parse_ask(raw: Any) -> Optional[Decimal]:
    if raw is None or isinstance(raw, bool):
        return None
    try:
        ask = Decimal(str(raw))
    except Exception:
        return None
    if ask <= 0:
        return None
    return ask


def ask_from_ticker_payload(data: Any) -> Decimal:
    if not isinstance(data, dict) or data.get("retCode") != 0:
        raise RuntimeError("Bybit USDCUSDT: retCode")
    result = data.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("Bybit USDCUSDT: нет result")
    rows = result.get("list")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise RuntimeError("Bybit USDCUSDT: пустой список")
    ask = parse_ask(rows[0].get("ask1Price"))
    if ask is None:
        raise RuntimeError("Bybit USDCUSDT: нет ask1Price")
    return ask


def fetch_ask_payload(*, timeout: float = 30.0) -> Dict[str, Any]:
    req = urllib.request.Request(TICKER_URL, headers=HEADERS, method="GET")
    ctx = ssl.create_default_context()
    try:
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            data = json.loads(resp.read().decode(resp.headers.get_content_charset() or "utf-8"))
        ask = ask_from_ticker_payload(data)
    except urllib.error.HTTPError as exc:
        return {"ask_usdt_per_usdc": None, "warnings": [f"Bybit USDCUSDT: HTTP {exc.code}"]}
    except urllib.error.URLError as exc:
        return {"ask_usdt_per_usdc": None, "warnings": [f"Bybit USDCUSDT: {exc}"]}
    except (RuntimeError, json.JSONDecodeError) as exc:
        return {"ask_usdt_per_usdc": None, "warnings": [f"Bybit USDCUSDT: {exc}"]}
    return {"ask_usdt_per_usdc": format(ask, "f"), "warnings": []}
