# -*- coding: utf-8 -*-
"""Senate Exchange: RUB→THB и USDT→THB по калькулятору senateexchange.com."""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "senate"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.EXCHANGER

SITE_URL = "https://senateexchange.com/"
# Калькулятор на Tilda (inline JS).
LEGACY_PRICES_URL = "https://excenge.store/api/exchange_prices"
# Backend CRM (закрыт без Authorization, см. SENATE_AUTHORIZATION).
CRM_PRICES_URL = (
    "https://crm.senateexchange.com/api/client_app_IUBTY/v1/rates/exchange_prices/"
)
_CACHE_FILE = (
    Path(__file__).resolve().parents[1] / ".rates_cache" / "senate_prices.json"
)
PAIR_TITLE = "RUB-THB"
# Сайт: при получении меньше этой суммы курс хуже и в API его нет.
MIN_PUBLISHED_THB = 10_000.0
# income_percents["RUB-online__THB-nal"] на главной Tilda.
RUB_ONLINE_THB_NAL_PERCENT = 2.5
# withdraw_methods_percents["USDT_THB_atm"].
USDT_THB_ATM_PERCENT = -2.5
_DEFAULT_CACHE_MAX_AGE_SEC = 7200.0
_UA = "rates-senate-source/1.0 (python)"


def _cache_file() -> Path:
    raw = (os.environ.get("SENATE_PRICES_CACHE_FILE") or "").strip()
    return Path(raw) if raw else _CACHE_FILE


def _cache_max_age_sec() -> float:
    raw = (os.environ.get("SENATE_PRICES_CACHE_MAX_AGE_SEC") or "").strip()
    if not raw:
        return _DEFAULT_CACHE_MAX_AGE_SEC
    try:
        return max(60.0, float(raw))
    except ValueError:
        return _DEFAULT_CACHE_MAX_AGE_SEC


def prices_urls() -> List[str]:
    custom = (os.environ.get("SENATE_PRICES_URL") or "").strip()
    if custom:
        return [custom]
    urls: List[str] = [LEGACY_PRICES_URL]
    if (os.environ.get("SENATE_AUTHORIZATION") or "").strip():
        urls.append(CRM_PRICES_URL)
    return urls


def prices_url() -> str:
    urls = prices_urls()
    return urls[0]


def help_text() -> str:
    return (
        "Senate Exchange: GET exchange_prices как на Tilda "
        f"({LEGACY_PRICES_URL}). "
        "Переопределение: SENATE_PRICES_URL. "
        "CRM (только с SENATE_AUTHORIZATION): "
        f"{CRM_PRICES_URL}. "
        f"Кеш: {_cache_file()} (browser/senate_prices_cdp.py). "
        f"RUB→THB: RUB-online/THB-nal (+{RUB_ONLINE_THB_NAL_PERCENT:g}%). "
        f"USDT→THB ATM: thb_for_usdt_sell ({USDT_THB_ATM_PERCENT:g}%). "
        f"Курс RUB→THB опубликован от {MIN_PUBLISHED_THB:.0f} THB."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def add_percent(value: float, percent: float) -> float:
    """Как add_percent() в inline-скрипте калькулятора на сайте."""
    return round(value * (1 + percent / 100), 5)


def _request_headers(*, for_url: str) -> Dict[str, str]:
    hdr = {
        "Accept": "application/json",
        "User-Agent": _UA,
        "Referer": f"{SITE_URL.rstrip('/')}/",
        "Origin": SITE_URL.rstrip("/"),
    }
    auth = (os.environ.get("SENATE_AUTHORIZATION") or "").strip()
    if auth:
        hdr["Authorization"] = auth
    if for_url.startswith(CRM_PRICES_URL):
        hdr["Referer"] = f"{SITE_URL.rstrip('/')}/"
    return hdr


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


def validate_prices_payload(data: Dict[str, Any]) -> None:
    _price_field(data, "rub_for_usdt_buy")
    _price_field(data, "thb_for_usdt_sell")


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


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    detail = exc.read()[:200].decode("utf-8", errors="replace")
    if detail.lstrip().startswith("<"):
        return f"{detail[:80].strip()}…"
    return detail


def _fetch_prices_url(url: str, *, timeout: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=_request_headers(for_url=url))
    try:
        with urlopen_retriable(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as e:
        hint = ""
        if e.code == 403 and url.startswith("https://crm."):
            hint = " (CRM без публичного доступа; задайте SENATE_AUTHORIZATION или SENATE_PRICES_URL)"
        raise RuntimeError(
            f"Senate prices HTTP {e.code}{hint}: {_http_error_detail(e)}"
        ) from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        raise RuntimeError(f"Senate prices URL {url}: {reason}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Senate: не JSON в ответе {raw[:120]!r}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"Senate: неожиданный ответ {type(data).__name__}")
    validate_prices_payload(data)
    return data


def save_prices_cache(data: Dict[str, Any], *, path: Optional[Path] = None) -> None:
    target = path or _cache_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"saved_unix": time.time(), "prices": data},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _load_prices_cache(*, max_age_sec: Optional[float] = None) -> Dict[str, Any]:
    path = _cache_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(f"Senate: кеш {path} недоступен") from e
    if not isinstance(raw, dict):
        raise RuntimeError(f"Senate: битый кеш {path}")
    saved = raw.get("saved_unix")
    prices = raw.get("prices")
    if not isinstance(prices, dict):
        raise RuntimeError(f"Senate: в кеше нет prices ({path})")
    age_limit = max_age_sec if max_age_sec is not None else _cache_max_age_sec()
    if isinstance(saved, (int, float)):
        age = time.time() - float(saved)
        if age > age_limit:
            raise RuntimeError(
                f"Senate: кеш устарел ({age:.0f}s > {age_limit:.0f}s)"
            )
    validate_prices_payload(prices)
    return prices


def fetch_prices(*, timeout: float = 20.0, allow_cache: bool = True) -> Dict[str, Any]:
    errors: List[str] = []
    for url in prices_urls():
        try:
            data = _fetch_prices_url(url, timeout=timeout)
            try:
                save_prices_cache(data)
            except OSError:
                pass
            return data
        except Exception as e:
            errors.append(str(e))
    if allow_cache:
        try:
            return _load_prices_cache()
        except Exception as e:
            errors.append(str(e))
    raise RuntimeError("; ".join(errors))


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
