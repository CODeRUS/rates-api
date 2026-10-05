# -*- coding: utf-8 -*-
"""XAACTA: RUB→THB из публичного калькулятора миниаппа (api.xaacta.com)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "xaacta"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.EXCHANGER

API_BASE = "https://api.xaacta.com"
CALC_PATH = "/api/public/exchange/calc"
# Совпадает с rates.DEFAULT_THB_REF: сводка считает получение этой суммы THB.
DEFAULT_RECEIVING_THB = 30_000.0
_UA = "rates-xaacta-source/1.0 (python)"


def help_text() -> str:
    return (
        "XAACTA RUB→THB: GET "
        f"{API_BASE}{CALC_PATH}?from=RUB&to=THB&amount=&amountSide=TO. "
        "В сводке сумма — --receiving-thb. Курс: RUB за 1 THB."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _amount_param(amount: float) -> str:
    if amount == int(amount):
        return str(int(amount))
    text = f"{amount:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def parse_rub_per_thb(data: Dict[str, Any]) -> float:
    """``amountFrom / amountTo`` — рубли за 1 THB. Поле ``rate`` в API — THB за 1 RUB."""
    try:
        amount_from = float(data["amountFrom"])
        amount_to = float(data["amountTo"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"XAACTA: нет amountFrom/amountTo в {data!r}") from e
    if amount_from <= 0 or amount_to <= 0:
        raise RuntimeError(f"XAACTA: невалидные суммы from={amount_from} to={amount_to}")
    return amount_from / amount_to


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body[:200]
    if isinstance(data, dict):
        msg = str(data.get("message") or "").strip()
        code = str(data.get("code") or "").strip()
        if msg and code:
            return f"{code}: {msg}"
        return msg or code or body[:200]
    return body[:200]


def fetch_calc(
    amount: float,
    amount_side: str = "TO",
    *,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    qs = urllib.parse.urlencode(
        {
            "from": "RUB",
            "to": "THB",
            "amount": _amount_param(amount),
            "amountSide": amount_side,
        }
    )
    url = f"{API_BASE}{CALC_PATH}?{qs}"
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": _UA},
    )
    try:
        with urlopen_retriable(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as e:
        detail = _error_message(e.read().decode("utf-8", errors="replace"))
        raise RuntimeError(f"XAACTA HTTP {e.code}: {detail}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"XAACTA: не JSON в ответе {raw[:120]!r}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"XAACTA: неожиданный ответ {type(data).__name__}")
    return data


def summary(ctx: FetchContext) -> Optional[list]:
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and float(ctx.receiving_thb) > 0)
        else DEFAULT_RECEIVING_THB
    )
    try:
        data = fetch_calc(target_thb, "TO")
        rate = parse_rub_per_thb(data)
    except Exception as e:
        ctx.warnings.append(f"xaacta: {e}")
        return None
    return [
        SourceQuote(
            rate,
            "XAACTA",
            note=f"≈ {fmt_money_ru(target_thb)} THB",
            category=SourceCategory.EXCHANGER,
            emoji=EMOJI,
        )
    ]
