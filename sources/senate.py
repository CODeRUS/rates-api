# -*- coding: utf-8 -*-
"""Senate Exchange: RUB→THB из калькулятора senateexchange.org."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "senate"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.EXCHANGER

RATES_URL = "https://senateexchange.org/wp-json/my-proxy/v1/rates"
PAIR_TITLE = "RUB-THB"
# Сайт: при получении меньше этой суммы курс хуже и в API его нет.
MIN_PUBLISHED_THB = 10_000.0
_UA = "rates-senate-source/1.0 (python)"


def help_text() -> str:
    return (
        "Senate Exchange RUB→THB: GET "
        f"{RATES_URL}. Пара {PAIR_TITLE}, ratio — RUB за 1 THB "
        f"(если is_client_base=false). Курс опубликован от {MIN_PUBLISHED_THB:.0f} THB."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def rub_per_thb_from_pair(pair: Dict[str, Any]) -> float:
    """
    Калькулятор сайта: THB = RUB * ratio, если is_client_base, иначе THB = RUB / ratio.
    В сводку нужно RUB за 1 THB.
    """
    try:
        ratio = float(pair["ratio"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"Senate: нет ratio в {pair!r}") from e
    if ratio <= 0:
        raise RuntimeError(f"Senate: невалидный ratio={ratio}")
    if pair.get("is_client_base"):
        return 1.0 / ratio
    return ratio


def thb_per_base_from_pair(pair: Dict[str, Any]) -> float:
    """THB за 1 единицу базовой валюты. При is_client_base: THB = base * ratio."""
    try:
        ratio = float(pair["ratio"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"Senate: нет ratio в {pair!r}") from e
    if ratio <= 0:
        raise RuntimeError(f"Senate: невалидный ratio={ratio}")
    if pair.get("is_client_base"):
        return ratio
    return 1.0 / ratio


def find_pair_by_title(data: Dict[str, Any], title: str) -> Dict[str, Any]:
    payload = data.get("data")
    pairs = payload.get("currency_pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list):
        raise RuntimeError("Senate: нет data.currency_pairs")
    for pair in pairs:
        if isinstance(pair, dict) and pair.get("title") == title:
            return pair
    raise RuntimeError(f"Senate: нет пары {title}")


def _find_pair(data: Dict[str, Any]) -> Dict[str, Any]:
    return find_pair_by_title(data, PAIR_TITLE)


def fetch_rates(*, timeout: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        RATES_URL,
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
        detail = e.read()[:200].decode("utf-8", errors="replace")
        raise RuntimeError(f"Senate HTTP {e.code}: {detail}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Senate: не JSON в ответе {raw[:120]!r}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"Senate: неожиданный ответ {type(data).__name__}")
    return data


def summary(ctx: FetchContext) -> Optional[list]:
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and float(ctx.receiving_thb) > 0)
        else None
    )
    if target_thb is not None and target_thb < MIN_PUBLISHED_THB:
        ctx.warnings.append(
            f"senate: опубликованный курс для сумм от {MIN_PUBLISHED_THB:.0f} THB, "
            f"запрошено {target_thb:g}"
        )
    try:
        pair = _find_pair(fetch_rates())
        rate = rub_per_thb_from_pair(pair)
    except Exception as e:
        ctx.warnings.append(f"senate: {e}")
        return None
    return [
        SourceQuote(
            rate,
            "Senate",
            note="от 10 000 THB",
            category=SourceCategory.EXCHANGER,
            emoji=EMOJI,
        )
    ]
