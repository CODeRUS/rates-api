# -*- coding: utf-8 -*-
"""Bangkok Bank: GetLatestfxrates — TT для номинала USD50 (THB за 1 USD).

По умолчанию используется **curl_cffi** (если установлен): тот же подход, что у Bybit — иначе urllib из Docker
часто получает ``The read operation timed out``. Принудительно stdlib: ``BANGKOKBANK_HTTP_CLIENT=urllib``.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from rates_http import (
    RETRYABLE_HTTP_CODES,
    RetryableHttpStatus,
    backoff_base_sec,
    call_retriable,
    max_attempts,
    urlopen_retriable,
)

logger = logging.getLogger(__name__)

BBL_LATEST_URL = "https://www.bangkokbank.com/api/exchangerateservice/GetLatestfxrates"

# API с «дальних» сетей и из Docker часто не укладывается в короткий read-timeout (ответ JSON большой).
_BBL_TIMEOUT_DEFAULT_HOST = 75.0
_BBL_TIMEOUT_DEFAULT_DOCKER = 240.0
_BBL_ATTEMPTS_MIN = 5
_BBL_ATTEMPTS_DOCKER_FLOOR = 8


def _in_docker() -> bool:
    return os.path.isfile("/.dockerenv")


def bbl_http_timeout_sec() -> float:
    """Таймаут чтения ответа (сек). ``BANGKOKBANK_HTTP_TIMEOUT_SEC``; в Docker — 240, если не задано."""
    raw = (os.environ.get("BANGKOKBANK_HTTP_TIMEOUT_SEC") or "").strip()
    if raw:
        try:
            v = float(raw)
            return max(15.0, min(600.0, v))
        except ValueError:
            pass
    if _in_docker():
        return _BBL_TIMEOUT_DEFAULT_DOCKER
    return _BBL_TIMEOUT_DEFAULT_HOST


def bbl_urlopen_max_attempts() -> int:
    """Повторы при обрыве/таймауте. ``BANGKOKBANK_HTTP_MAX_ATTEMPTS``; в Docker — не меньше 8, если не задано."""
    raw = (os.environ.get("BANGKOKBANK_HTTP_MAX_ATTEMPTS") or "").strip()
    if raw:
        try:
            return max(1, min(12, int(raw)))
        except ValueError:
            pass
    n = max(_BBL_ATTEMPTS_MIN, max_attempts())
    if _in_docker():
        n = max(n, _BBL_ATTEMPTS_DOCKER_FLOOR)
    return n


def bbl_urlopen_backoff_base() -> float:
    """Пауза между повторами (сек). ``BANGKOKBANK_HTTP_BACKOFF_BASE``; в Docker по умолчанию 1.0."""
    raw = (os.environ.get("BANGKOKBANK_HTTP_BACKOFF_BASE") or "").strip()
    if raw:
        try:
            return max(0.1, float(raw))
        except ValueError:
            pass
    return 1.0 if _in_docker() else backoff_base_sec()


def bbl_api_headers(*, subscription_key: str) -> Dict[str, str]:
    """Заголовки как у браузера; ``Referer`` обязателен для стабильного ответа."""
    return {
        "accept-language": "en-US,en;q=0.9,ru;q=0.8",
        "ocp-apim-subscription-key": subscription_key,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "accept": "application/json",
        "referer": "https://www.bangkokbank.com/",
    }


def subscription_key_from_env() -> str:
    return (os.environ.get("BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY") or "").strip()


def _as_rate_list(data: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "rates", "result", "items", "value"):
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [x for x in v if isinstance(x, dict)]
    return None


def parse_usd50_tt_thb(rows: List[Dict[str, Any]]) -> Optional[float]:
    """THB за 1 USD (TT) для ``Family == USD50``."""
    for row in rows:
        fam = row.get("Family") if "Family" in row else row.get("family")
        if fam is None or str(fam).strip().upper() != "USD50":
            continue
        tt = row.get("TT") if "TT" in row else row.get("tt")
        if tt is None:
            return None
        s = str(tt).strip().replace("\u00a0", "").replace(" ", "")
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            s = s.replace(",", ".")
        try:
            v = float(s)
        except ValueError:
            return None
        return v if v > 0 else None
    return None


def _bbl_prefer_curl_cffi() -> bool:
    """``curl_cffi`` даёт браузерный TLS (как у Bybit); urllib из Docker часто ловит read timeout к BBL."""
    if (os.environ.get("BANGKOKBANK_HTTP_CLIENT") or "").strip().lower() in (
        "urllib",
        "stdlib",
    ):
        return False
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return False
    return True


def _fetch_latest_rates_json_urllib(subscription_key: str, t: float) -> Any:
    req = urllib.request.Request(
        BBL_LATEST_URL,
        headers=bbl_api_headers(subscription_key=subscription_key),
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urlopen_retriable(
        req,
        timeout=t,
        context=ctx,
        max_attempts_override=bbl_urlopen_max_attempts(),
        backoff_override=bbl_urlopen_backoff_base(),
    ) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    return json.loads(raw)


def _fetch_latest_rates_json_curl_cffi(subscription_key: str, t: float) -> Any:
    from curl_cffi import requests as cr  # type: ignore[import-not-found]

    headers = bbl_api_headers(subscription_key=subscription_key)
    connect_t = min(60.0, max(15.0, t * 0.35))
    timeout_pair = (connect_t, t)

    raw_imp = (os.environ.get("BANGKOKBANK_CURL_IMPERSONATE") or "").strip()
    if raw_imp:
        imps = [x.strip() for x in raw_imp.split(",") if x.strip()]
    else:
        imps = ["chrome124", "chrome131", "chrome120"]

    last: Optional[BaseException] = None
    for imp in imps:

        def one_shot(impersonate: str = imp) -> Any:
            r = cr.get(
                BBL_LATEST_URL,
                headers=headers,
                timeout=timeout_pair,
                impersonate=impersonate,
            )
            if r.status_code in RETRYABLE_HTTP_CODES:
                raise RetryableHttpStatus(r.status_code)
            if r.status_code != 200:
                raise RuntimeError(
                    f"Bangkok Bank HTTP {r.status_code}: {(r.text or '')[:400]!r}"
                )
            return json.loads(r.text)

        try:
            return call_retriable(
                one_shot,
                max_attempts_override=bbl_urlopen_max_attempts(),
                backoff_override=bbl_urlopen_backoff_base(),
            )
        except Exception as e:
            last = e
            logger.info("Bangkok Bank curl_cffi impersonate=%r не удалось: %s", imp, e)
            continue
    if last:
        raise last
    raise RuntimeError("Bangkok Bank curl_cffi: пустой BANGKOKBANK_CURL_IMPERSONATE")


def fetch_latest_rates_json(
    *,
    subscription_key: Optional[str] = None,
    timeout: Optional[float] = None,
) -> Any:
    key = (subscription_key or subscription_key_from_env()).strip()
    if not key:
        raise RuntimeError("Нет ключа Bangkok Bank (BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY)")
    t = float(timeout) if timeout is not None else bbl_http_timeout_sec()
    if _bbl_prefer_curl_cffi():
        try:
            return _fetch_latest_rates_json_curl_cffi(key, t)
        except Exception as e:
            logger.warning("Bangkok Bank: curl_cffi ошибка, fallback urllib: %s", e)
    return _fetch_latest_rates_json_urllib(key, t)


def fetch_usd50_tt_thb(
    *,
    subscription_key: Optional[str] = None,
    timeout: Optional[float] = None,
) -> float:
    data = fetch_latest_rates_json(subscription_key=subscription_key, timeout=timeout)
    rows = _as_rate_list(data)
    if not rows:
        raise RuntimeError("Bangkok Bank: нет списка котировок в ответе")
    tt = parse_usd50_tt_thb(rows)
    if tt is None:
        raise RuntimeError("Bangkok Bank: нет записи Family=USD50 с полем TT")
    return tt
