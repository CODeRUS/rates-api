# -*- coding: utf-8 -*-
"""Bangkok Bank: GetLatestfxrates — TT для номинала USD50 (THB за 1 USD)."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable

BBL_LATEST_URL = "https://www.bangkokbank.com/api/exchangerateservice/GetLatestfxrates"


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


def fetch_latest_rates_json(
    *,
    subscription_key: Optional[str] = None,
    timeout: float = 35.0,
) -> Any:
    key = (subscription_key or subscription_key_from_env()).strip()
    if not key:
        raise RuntimeError("Нет ключа Bangkok Bank (BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY)")
    req = urllib.request.Request(
        BBL_LATEST_URL,
        headers=bbl_api_headers(subscription_key=key),
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    return json.loads(raw)


def fetch_usd50_tt_thb(
    *,
    subscription_key: Optional[str] = None,
    timeout: float = 35.0,
) -> float:
    data = fetch_latest_rates_json(subscription_key=subscription_key, timeout=timeout)
    rows = _as_rate_list(data)
    if not rows:
        raise RuntimeError("Bangkok Bank: нет списка котировок в ответе")
    tt = parse_usd50_tt_thb(rows)
    if tt is None:
        raise RuntimeError("Bangkok Bank: нет записи Family=USD50 с полем TT")
    return tt
