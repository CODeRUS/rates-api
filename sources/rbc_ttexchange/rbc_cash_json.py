# -*- coding: utf-8 -*-
"""Загрузка и разбор JSON cash.rbc.ru (наличные курсы банков)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from rates_http import urlopen_retriable

RBC_CASH_URL = "https://cash.rbc.ru/cash/json/cash_rates_with_volumes/"
USER_AGENT = "rates-rbc-cash/1.0 (python)"


def fetch_cash_rates_json(
    *,
    city: int,
    currency_id: int,
    volume: int = 0,
    timeout: float = 22.0,
) -> Optional[Dict[str, Any]]:
    qs = urllib.parse.urlencode(
        {"city": city, "currency": currency_id, "volume": volume, "_": "1"}
    )
    url = f"{RBC_CASH_URL}?{qs}"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
        return json.loads(raw)
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        json.JSONDecodeError,
        TimeoutError,
    ):
        return None


def min_sell_rub_per_unit(banks: Any) -> Tuple[Optional[float], str]:
    """
    Минимальное ``rate.sell`` по отделениям — RUB за 1 единицу валюты (банк продаёт валюту клиенту).
    Возвращает (значение, название отделения с этим курсом).
    """
    if not isinstance(banks, list):
        return None, ""
    best: Optional[float] = None
    best_name = ""
    for b in banks:
        if not isinstance(b, dict):
            continue
        r = b.get("rate")
        if not isinstance(r, dict):
            continue
        s = r.get("sell")
        if s is None:
            continue
        try:
            v = float(str(s).replace(",", ".").replace(" ", ""))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        if best is None or v < best:
            best = v
            best_name = str(b.get("name") or "").strip()
    return best, best_name
