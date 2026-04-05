# -*- coding: utf-8 -*-
"""Публичный кеш курсов Moreta (THB за 1 USD/USDT)."""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
from typing import Any, Optional, Tuple

from rates_http import urlopen_retriable

DEFAULT_URL = "https://api-cache.moreta.io/exchange-rates"
USER_AGENT = "rates-api/moreta/1.0 (python)"


def moreta_rates_url() -> str:
    raw = (os.environ.get("MORETA_EXCHANGE_RATES_URL") or "").strip()
    return raw or DEFAULT_URL


def _get_json(url: str, *, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode(
            resp.headers.get_content_charset() or "utf-8", errors="replace"
        )
    return json.loads(raw)


def fetch_thb_per_usdt(*, timeout: float = 20.0, url: Optional[str] = None) -> Tuple[Optional[float], str]:
    """
    THB за 1 USDT из ``rates.USD_THB`` (как на moreta.io).
    """
    u = url or moreta_rates_url()
    try:
        data = _get_json(u, timeout=timeout)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return None, f"Moreta rates: {e}"
    if not isinstance(data, dict):
        return None, "Moreta rates: не объект JSON"
    rates = data.get("rates")
    if not isinstance(rates, dict):
        return None, "Moreta rates: нет поля rates"
    raw_v = rates.get("USD_THB")
    if raw_v is None:
        return None, "Moreta rates: нет USD_THB"
    try:
        r = float(raw_v)
    except (TypeError, ValueError):
        return None, f"Moreta rates: невалидный USD_THB={raw_v!r}"
    if r <= 0:
        return None, "Moreta rates: USD_THB<=0"
    return r, ""
