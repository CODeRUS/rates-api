# -*- coding: utf-8 -*-
"""Базовый курс THB→RUB для сводки: XE (midmarket / paid API) с fallback на ExchangeRate-API."""
from __future__ import annotations

import os
import urllib.error
from typing import Tuple

from . import forex_er_api as er
from . import forex_xe_api as xe

ProviderTag = str


def _paid_xe_thb_rub() -> float:
    aid = os.environ.get("XE_ACCOUNT_ID", "").strip()
    key = os.environ.get("XE_API_KEY", "").strip()
    if not aid or not key:
        raise RuntimeError("XE paid API credentials not configured")
    data = xe.convert_from("THB", ["RUB"], 1.0, account_id=aid, api_key=key)
    rows = data.get("to") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("XE convert_from: пустой блок to")
    for row in rows:
        if not isinstance(row, dict):
            continue
        qc = str(row.get("quotecurrency", "")).upper()
        if qc == "RUB":
            mid = row.get("mid")
            if mid is not None:
                return float(mid)
    raise KeyError("RUB")


def thb_rub_rate(*, timeout: float = 30.0) -> Tuple[float, ProviderTag]:
    """
    RUB за 1 THB для baseline Forex.

    Порядок: XE midmarket → XE paid (если есть ключи) → ExchangeRate-API (open.er-api.com).
    """
    try:
        conv = xe.midmarket_convert("THB", "RUB", 1.0, timeout=timeout)
        return float(conv["result"]), "xe_midmarket"
    except KeyError:
        pass
    except (RuntimeError, urllib.error.URLError, OSError, urllib.error.HTTPError):
        pass

    try:
        return _paid_xe_thb_rub(), "xe_paid"
    except (RuntimeError, KeyError, urllib.error.URLError, OSError, urllib.error.HTTPError):
        pass

    rate = er.convert(1.0, "THB", "RUB", timeout=timeout)
    return float(rate), "er_api"
