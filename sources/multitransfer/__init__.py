# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import urllib.request
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "multitransfer"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_API_URL = "https://api.multitransfer.ru/anonymous/multi/multitransfer-fee-calc/v3/commissions"
_CLIENT_ID = "multitransfer-web-id"
_DEFAULT_X_REQUEST_ID = "894aa54e-a7bf-4701-a511-4ff8b59560eb"
_DEFAULT_FHP_SESSION_ID = "e2c657da-a35b-4bb3-abcc-be6288602aba"
_DEFAULT_FHP_REQUEST_ID = "aba4f640-3eed-49ed-89ac-9ba1d9d383c1"


def help_text() -> str:
    return "Мультитрансфер RUB→THB. Полные опции: multitransfer --help"


def _request_commissions(target_thb: float, *, timeout: float = 25.0) -> Dict[str, Any]:
    body = {
        "countryCode": "THA",
        "range": "ALL_PLUS_LIMITS",
        "money": {
            "acceptedMoney": {"currencyCode": "RUB"},
            "withdrawMoney": {"currencyCode": "THB", "amount": int(round(target_thb))},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Client-Id": _CLIENT_ID,
        # API валидирует набор идентификаторов; случайные UUID часто дают 423/103.
        "X-Request-Id": _DEFAULT_X_REQUEST_ID,
        "FhpSessionId": _DEFAULT_FHP_SESSION_ID,
        "FhpRequestId": _DEFAULT_FHP_REQUEST_ID,
        "User-Agent": "Mozilla/5.0",
    }
    # Для этого API urllib в ряде сред стабильно получает 423 (WAF/anti-bot),
    # а browser-like TLS fingerprint через curl_cffi проходит.
    try:
        from curl_cffi import requests as cffi_requests

        resp = cffi_requests.post(
            _API_URL,
            headers=headers,
            json=body,
            impersonate="chrome124",
            timeout=timeout,
        )
        if int(resp.status_code) >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        if not isinstance(data, dict):
            raise RuntimeError("Multitransfer: неверный формат ответа")
        err = data.get("error")
        if isinstance(err, dict) and int(err.get("code") or 0) != 0:
            raise RuntimeError(
                f"Multitransfer API code={err.get('code')}: {err.get('message') or 'unknown error'}"
            )
        return data
    except Exception:
        # fallback на urllib для совместимости окружений без curl_cffi
        pass

    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    with urlopen_retriable(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Multitransfer: неверный формат ответа")
    err = data.get("error")
    if isinstance(err, dict) and int(err.get("code") or 0) != 0:
        raise RuntimeError(
            f"Multitransfer API code={err.get('code')}: {err.get('message') or 'unknown error'}"
        )
    return data


def _iter_commissions(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    fees = data.get("fees")
    if not isinstance(fees, list):
        return out
    for f in fees:
        if not isinstance(f, dict):
            continue
        cc = f.get("commissions")
        if not isinstance(cc, list):
            continue
        for c in cc:
            if isinstance(c, dict):
                out.append(c)
    return out


def _rub_per_thb(c: Dict[str, Any]) -> Optional[float]:
    money = c.get("money")
    if not isinstance(money, dict):
        return None
    acc = money.get("acceptedMoney")
    wd = money.get("withdrawMoney")
    if not isinstance(acc, dict) or not isinstance(wd, dict):
        return None
    try:
        rub = float(acc.get("amount") or 0.0)
        thb = float(wd.get("amount") or 0.0)
    except (TypeError, ValueError):
        return None
    if rub <= 0 or thb <= 0:
        return None
    return rub / thb


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and ctx.receiving_thb > 0)
        else 10_000.0
    )
    note = f"≈ {fmt_money_ru(target_thb)} THB"
    try:
        data = _request_commissions(target_thb)
    except Exception as e:
        ctx.warnings.append(f"Multitransfer: {e}")
        return None

    commissions = _iter_commissions(data)
    if not commissions:
        return None

    bank_row: Optional[SourceQuote] = None
    account_row: Optional[SourceQuote] = None
    for c in commissions:
        name_cyr = str(c.get("nameCyrillic") or "")
        name_lat = str(c.get("nameLat") or "")
        rate = _rub_per_thb(c)
        if rate is None:
            continue
        if bank_row is None and "BANGKOK BANK" in name_lat.upper():
            bank_row = SourceQuote(rate, "Мультитрансфер - Банк")
        if account_row is None and "номеру счета" in name_cyr.lower():
            account_row = SourceQuote(rate, "Мультитрансфер - Счет")

    out: List[SourceQuote] = []
    if bank_row is not None:
        out.append(bank_row)
    if account_row is not None:
        out.append(account_row)
    return out or None


def command(argv: list[str]) -> int:
    import argparse

    p = argparse.ArgumentParser(
        prog="rates.py multitransfer",
        description="Мультитрансфер RUB→THB (анонимный калькулятор комиссий).",
    )
    p.add_argument("thb", nargs="?", type=float, default=10000.0, help="Сумма получения THB")
    args = p.parse_args(argv)
    try:
        data = _request_commissions(float(args.thb))
        rows = _iter_commissions(data)
        print(f"Multitransfer, цель: {args.thb:g} THB")
        for c in rows:
            r = _rub_per_thb(c)
            if r is None:
                continue
            name = str(c.get("nameCyrillic") or c.get("nameLat") or "—")
            print(f"{r:.6f} RUB/THB | {name}")
        return 0
    except Exception as e:
        print(f"Multitransfer: {e}")
        return 1
