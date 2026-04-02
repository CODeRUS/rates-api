# -*- coding: utf-8 -*-
"""Загрузка и разбор JSON cash.rbc.ru (наличные курсы банков)."""
from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from rates_http import urlopen_retriable

logger = logging.getLogger(__name__)

from sources.rbc_bank_title import rbc_short_bank_name

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
        logger.info(
            "rbc_cash http GET start city=%s currency_id=%s timeout=%.1fs",
            city,
            currency_id,
            timeout,
        )
        t0 = time.perf_counter()
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
        logger.info(
            "rbc_cash http GET done city=%s currency_id=%s in %.2fs (%d bytes)",
            city,
            currency_id,
            time.perf_counter() - t0,
            len(raw.encode("utf-8")),
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


def bank_sell_rows(banks: Any) -> List[Tuple[float, str]]:
    """
    Все отделения с валидным ``rate.sell``, сортировка по **возрастанию** sell
    (меньше RUB за единицу — выгоднее покупка валюты у банка).
    """
    if not isinstance(banks, list):
        return []
    rows: List[Tuple[float, str]] = []
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
        rows.append((v, str(b.get("name") or "").strip()))
    rows.sort(key=lambda t: (t[0], t[1]))
    return rows


def top_sell_offers(
    banks: Any,
    n: int = 3,
) -> List[Tuple[float, str, str]]:
    """
    До ``n`` уникальных пар (курс, короткое имя банка) в порядке сортировки по sell:
    ``(sell, raw_office_name, short_bank)``.

    Несколько отделений одного банка с одним и тем же ``sell`` дают одну строку
    (берётся первое отделение в порядке сортировки по ``name``).
    """
    out: List[Tuple[float, str, str]] = []
    seen: set[Tuple[float, str]] = set()
    for sell, raw in bank_sell_rows(banks):
        short = rbc_short_bank_name(raw)
        label = (short or raw or "—").strip()
        key = (round(sell, 6), label.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append((sell, raw, label))
        if len(out) >= n:
            break
    return out
