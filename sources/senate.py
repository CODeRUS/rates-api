# -*- coding: utf-8 -*-
"""Senate Exchange: RUB→THB и USDT→THB по калькулятору senateexchange.com."""
from __future__ import annotations

import json
import os
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

SITE_URL = "https://senateexchange.com/"
DEFAULT_PRICES_URL = "https://excenge.store/api/exchange_prices"
PAIR_TITLE = "RUB-THB"
# Сайт: при получении меньше этой суммы курс хуже и в API его нет.
MIN_PUBLISHED_THB = 10_000.0
# income_percents["RUB-online__THB-nal"] на главной Tilda.
RUB_ONLINE_THB_NAL_PERCENT = 2.5
# withdraw_methods_percents["USDT_THB_atm"].
USDT_THB_ATM_PERCENT = -2.5
_UA = "rates-senate-source/1.0 (python)"


def prices_url() -> str:
    raw = (os.environ.get("SENATE_PRICES_URL") or "").strip()
    return raw or DEFAULT_PRICES_URL


def help_text() -> str:
    return (
        "Senate Exchange: GET "
        f"{prices_url()}. RUB→THB: RUB-online/THB-nal (+{RUB_ONLINE_THB_NAL_PERCENT:g}%). "
        f"USDT→THB ATM: thb_for_usdt_sell ({USDT_THB_ATM_PERCENT:g}%). "
        f"Курс RUB→THB опубликован от {MIN_PUBLISHED_THB:.0f} THB."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def add_percent(value: float, percent: float) -> float:
    """Как add_percent() в inline-скрипте калькулятора на сайте."""
    return round(value * (1 + percent / 100), 5)


def _price_field(data: Dict[str, Any], key: str) -> float:
    block = data.get(key)
    if not isinstance(block, dict):
        raise RuntimeError(f"Senate: нет {key!r}")
    try:
        price = float(block["price"])
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"Senate: нет price в {key!r}") from e
    if price <= 0:
        raise RuntimeError(f"Senate: невалидный price в {key!r}={price}")
    return price


def rub_per_thb_from_prices(data: Dict[str, Any]) -> float:
    rub_usdt_buy = _price_field(data, "rub_for_usdt_buy")
    thb_usdt_sell = _price_field(data, "thb_for_usdt_sell")
    rub_thb = rub_usdt_buy / thb_usdt_sell
    return add_percent(rub_thb, RUB_ONLINE_THB_NAL_PERCENT)


def thb_per_usdt_atm_from_prices(data: Dict[str, Any]) -> float:
    thb_usdt_sell = _price_field(data, "thb_for_usdt_sell")
    return add_percent(thb_usdt_sell, USDT_THB_ATM_PERCENT)


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


def fetch_prices(*, timeout: float = 20.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        prices_url(),
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
        if detail.lstrip().startswith("<"):
            detail = f"{detail[:80].strip()}…"
        raise RuntimeError(f"Senate prices HTTP {e.code}: {detail}") from e
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
        rate = rub_per_thb_from_prices(fetch_prices())
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
