# -*- coding: utf-8 -*-
"""Курсы наличной продажи с Banki.ru (API getBanksOrExchanges, cookie + Referer)."""
from __future__ import annotations

import json
import random
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Dict, List, Optional

from rates_http import (
    RETRYABLE_HTTP_CODES,
    backoff_base_sec,
    is_retryable_exception,
    max_attempts,
)

USER_AGENT = "rates-banki-cash/1.0 (python)"
BANKI_ORIGIN = "https://www.banki.ru"
API_PATH = "/products/currencyNodejsApi/getBanksOrExchanges/"
MAX_PAGES = 40

# Ключ — slug в коде; regionUrl — как в API (у Казани ``kazan~``).
BANKI_REGIONS: Dict[str, Dict[str, object]] = {
    "moskva": {
        "regionUrl": "moskva",
        "regionId": 4,
        "sortAttribute": "sale",
        "order": "asc",
    },
    "sankt-peterburg": {
        "regionUrl": "sankt-peterburg",
        "regionId": 211,
        "sortAttribute": "recommend",
        "order": "desc",
    },
    "kazan": {
        "regionUrl": "kazan~",
        "regionId": 479,
        "sortAttribute": "recommend",
        "order": "desc",
    },
    "rostov-na-donu": {
        "regionUrl": "rostov-na-donu",
        "regionId": 345,
        "sortAttribute": "recommend",
        "order": "desc",
    },
    "novosibirsk": {
        "regionUrl": "novosibirsk",
        "regionId": 677,
        "sortAttribute": "recommend",
        "order": "desc",
    },
    "krasnoyarsk": {
        "regionUrl": "krasnoyarsk",
        "regionId": 657,
        "sortAttribute": "recommend",
        "order": "desc",
    },
}


def _sleep_backoff(attempt_index: int, *, base: float) -> None:
    delay = base * (2**attempt_index) + random.uniform(0, max(0.05, base * 0.25))
    time.sleep(delay)


def _opener_open_retriable(
    opener: urllib.request.OpenerDirector,
    req: urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    attempts = max_attempts()
    base = backoff_base_sec()
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return opener.open(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            last = e
            if attempt < attempts - 1 and e.code in RETRYABLE_HTTP_CODES:
                _sleep_backoff(attempt, base=base)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt < attempts - 1 and is_retryable_exception(e):
                _sleep_backoff(attempt, base=base)
                continue
            raise
    assert last is not None
    raise last


def _read_response_body(resp: Any) -> str:
    return resp.read().decode(
        resp.headers.get_content_charset() or "utf-8", errors="replace"
    )


def _bootstrap_referer_url(region_url: str, currency_id: int) -> str:
    path = f"/products/currency/cash/{region_url.strip('/')}/"
    qs = urllib.parse.urlencode({"currencyId": currency_id})
    return f"{BANKI_ORIGIN}{path}?{qs}"


def fetch_banki_banks_or_exchanges(
    *,
    region_url: str,
    region_id: int,
    currency_id: int,
    sort_attribute: str = "recommend",
    order: str = "desc",
    timeout: float = 22.0,
) -> Optional[Dict[str, Any]]:
    """
    Полный JSON ответа API (все страницы ``list`` объединены).
    Сначала загружается HTML страницы cash (cookies), затем XHR к API.
    """
    jar = CookieJar()
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )
    referer = _bootstrap_referer_url(region_url, currency_id)

    boot_req = urllib.request.Request(
        referer,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
    )
    try:
        with _opener_open_retriable(opener, boot_req, timeout=timeout) as boot_resp:
            _ = _read_response_body(boot_resp)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return None

    combined_list: List[Any] = []
    page = 1
    while page <= MAX_PAGES:
        qs = urllib.parse.urlencode(
            {
                "currencyId": currency_id,
                "regionUrl": region_url.strip("/"),
                "regionId": region_id,
                "page": page,
                "perPage": 50,
                "sortAttribute": sort_attribute,
                "order": order,
            }
        )
        api_url = f"{BANKI_ORIGIN}{API_PATH}?{qs}"
        api_req = urllib.request.Request(
            api_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json,*/*",
                "Referer": referer,
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with _opener_open_retriable(opener, api_req, timeout=timeout) as resp:
                raw = _read_response_body(resp)
            payload = json.loads(raw)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
            TimeoutError,
        ):
            return None
        if not isinstance(payload, dict):
            return None
        chunk = payload.get("list")
        if isinstance(chunk, list):
            combined_list.extend(chunk)
        if not payload.get("isNextPage"):
            break
        page += 1

    return {"list": combined_list}


def banki_sell_rows(payload: Any) -> List[tuple[float, str]]:
    """Список (sale RUB за 1 ед. валюты, имя банка) по ответу Banki (или только ``list``)."""
    lst: Any
    if isinstance(payload, dict):
        lst = payload.get("list")
    else:
        lst = payload
    if not isinstance(lst, list):
        return []
    rows: List[tuple[float, str]] = []
    for item in lst:
        if not isinstance(item, dict):
            continue
        ex = item.get("exchange")
        if not isinstance(ex, dict):
            continue
        s = ex.get("sale")
        if s is None:
            continue
        try:
            v = float(str(s).replace(",", ".").replace(" ", ""))
        except (TypeError, ValueError):
            continue
        if v <= 0:
            continue
        name = str(item.get("name") or "").strip() or "—"
        rows.append((v, name))
    rows.sort(key=lambda t: (t[0], t[1]))
    return rows
