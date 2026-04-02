# -*- coding: utf-8 -*-
"""HTTP-клиент к публичному API NovaWallet (курс THB/USDT, комиссии ledger)."""
from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from rates_http import urlopen_retriable

RATE_URL = "https://api.novawallet.org/ledger/rate"
LEDGER_URL = "https://api.novawallet.org/ledger"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; rates-api/1.0; +https://github.com/) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def _get_json(url: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(url, headers=dict(_DEFAULT_HEADERS), method="GET")
    ctx = ssl.create_default_context()
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    return json.loads(raw)


def fetch_thb_per_usdt(*, timeout: float = 20.0) -> Tuple[Optional[float], str]:
    """
    THB за 1 USDT из ``/ledger/rate``.

    Возвращает (значение или None, пояснение для warning при ошибке).
    """
    try:
        data = _get_json(RATE_URL, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return None, f"NovaWallet rate: {e}"

    row: Optional[Dict[str, Any]] = None
    if isinstance(data, dict):
        row = data
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        for it in data:
            if str((it or {}).get("currency") or "").strip().upper() == "THB":
                row = it
                break
        if row is None:
            row = data[0]  # type: ignore[assignment]

    if not isinstance(row, dict):
        return None, "NovaWallet rate: неожиданный JSON"

    cur = str(row.get("currency") or "").strip().upper()
    if cur and cur != "THB":
        return None, f"NovaWallet rate: currency={cur!r}, ожидался THB"

    try:
        r = float(row.get("rate") or 0)
    except (TypeError, ValueError):
        return None, "NovaWallet rate: невалидное поле rate"

    if r <= 0:
        return None, "NovaWallet rate: rate<=0"

    return r, ""


def _fees_from_ledger(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict):
        fees = data.get("fees")
        if isinstance(fees, list):
            return [f for f in fees if isinstance(f, dict)]
        if "operation" in data:
            return [data]
    if isinstance(data, list):
        return [f for f in data if isinstance(f, dict) and "operation" in f]
    return []


def fetch_cashout_fee_usd(
    *, timeout: float = 20.0
) -> Tuple[Optional[float], str]:
    """
    Фиксированная часть cashout в USD из ``/ledger`` (``operation == cashout``).
    """
    try:
        data = _get_json(LEDGER_URL, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return None, f"NovaWallet ledger: {e}"

    fees = _fees_from_ledger(data)
    for fee in fees:
        op = str(fee.get("operation") or "").strip().lower()
        if op != "cashout":
            continue
        try:
            u = float(fee.get("usd"))
        except (TypeError, ValueError):
            continue
        if u >= 0:
            return u, ""
    return None, "NovaWallet ledger: нет fees cashout.usd"
