# -*- coding: utf-8 -*-
"""Т-Банк: курс наличных RUB→THB по API витрины (ATM cash out)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "tbank"
EMOJI = "🏦"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

RATES_URL = "https://www.tbank.ru/api/common/v1/currency_rates?from=RUB&to=THB"
ATM_CASHOUT_CATEGORY = "ATMCashoutRateGroup"
USER_AGENT = "rates-tbank-source/1.0 (python)"


def help_text() -> str:
    return (
        "Т-Банк: наличные RUB→THB, категория ATMCashoutRateGroup, поле buy "
        f"({RATES_URL})."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def parse_atm_cashout_rub_per_thb(data: Dict[str, Any]) -> Optional[float]:
    """
    Из ответа API: запись ``ATMCashoutRateGroup`` RUB→THB, ``buy`` — THB за 1 RUB;
    в сводку нужно **RUB за 1 THB** → ``1 / buy``.
    """
    pl = data.get("payload")
    if not isinstance(pl, dict):
        return None
    rates = pl.get("rates")
    if not isinstance(rates, list):
        return None
    for row in rates:
        if not isinstance(row, dict):
            continue
        if row.get("category") != ATM_CASHOUT_CATEGORY:
            continue
        fc = row.get("fromCurrency")
        tc = row.get("toCurrency")
        if not isinstance(fc, dict) or not isinstance(tc, dict):
            continue
        if fc.get("name") != "RUB" or tc.get("name") != "THB":
            continue
        buy = row.get("buy")
        if buy is None:
            return None
        try:
            thb_per_rub = float(buy)
        except (TypeError, ValueError):
            return None
        if thb_per_rub <= 0:
            return None
        return 1.0 / thb_per_rub
    return None


def _load_rates_json(*, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        RATES_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        return json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    data = _load_rates_json()
    if data is None:
        ctx.warnings.append("tbank: не удалось загрузить currency_rates")
        return None
    if data.get("resultCode") != "OK":
        ctx.warnings.append(f"tbank: ответ API {data.get('resultCode')!r}")
        return None
    rub_per_thb = parse_atm_cashout_rub_per_thb(data)
    if rub_per_thb is None or rub_per_thb <= 0:
        ctx.warnings.append("tbank: нет ATMCashoutRateGroup RUB→THB или buy")
        return None
    return [
        SourceQuote(
            rub_per_thb,
            "Т-Банк",
            note="наличные в банкомате",
            category=SourceCategory.CASH_RUB,
            emoji="•",
            compare_to_baseline=True,
        )
    ]
