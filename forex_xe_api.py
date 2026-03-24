#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент к Xe: платный Xe Currency Data API и публичный midmarket-converter с сайта.

Платный API (нужны ключи):
  Документация: https://xecdapi.xe.com/docs/v1/
  Кабинет: https://currencydata.xe.com/account/dashboard

Публичный midmarket (как в запросах фронта xe.com, фиксированный Basic):
  GET https://www.xe.com/api/protected/midmarket-converter/?from=...&to=...&amount=...
  Заголовок Authorization задаётся константой ниже или переменной XE_MIDMARKET_AUTHORIZATION.

Примеры::

    python forex_xe_api.py midmarket USD THB 1
    python forex_xe_api.py midmarket RUB THB 100

    set XE_ACCOUNT_ID=...
    set XE_API_KEY=...
    python forex_xe_api.py convert RUB THB,USD 1
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Union

XE_API_BASE = "https://xecdapi.xe.com"
# Публичный эндпоинт конвертера (тот же Basic, что в запросах к сайту).
XE_MIDMARKET_URL = "https://www.xe.com/api/protected/midmarket-converter/"
XE_MIDMARKET_AUTHORIZATION_DEFAULT = "Basic bG9kZXN0YXI6cHVnc25heA=="

DEFAULT_TIMEOUT = 30
USER_AGENT = "forex-xe-api-client/1.0 (python)"


def _basic_auth_header(account_id: str, api_key: str) -> str:
    """Собирает заголовок Authorization: Basic … для HTTP Basic (как в документации XE)."""
    raw = f"{account_id}:{api_key}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def xe_get(
    path: str,
    *,
    account_id: str,
    api_key: str,
    params: Optional[Dict[str, Union[str, int, float, bool]]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    GET к Xe API: path вида ``/v1/convert_from``, параметры — query string.

    :raises urllib.error.HTTPError: 401 без/с неверными ключами, 429 при лимите и т.д.
    """
    path = path if path.startswith("/") else f"/{path}"
    url = XE_API_BASE.rstrip("/") + path
    if params:
        q = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None},
            doseq=True,
        )
        url = f"{url}?{q}"

    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": _basic_auth_header(account_id, api_key),
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def convert_from(
    from_currency: str,
    to_currencies: Sequence[str],
    amount: float = 1.0,
    *,
    account_id: str,
    api_key: str,
    **extra: Any,
) -> Any:
    """
    ``GET /v1/convert_from`` — пересчёт из ``from_currency`` в одну или несколько валют.

    ``to_currencies`` — например ``("THB", "USD")`` или одна строка ``"THB"``.
    Дополнительные query-параметры API (``interval``, ``decimal_places``, …) можно передать
    как ключевые аргументы в ``extra``.
    """
    to_param = ",".join(c.strip().upper() for c in to_currencies)
    params: Dict[str, Any] = {
        "from": from_currency.strip().upper(),
        "to": to_param,
        "amount": amount,
        **extra,
    }
    return xe_get("/v1/convert_from", account_id=account_id, api_key=api_key, params=params)


def load_credentials() -> tuple[str, str]:
    """Читает XE_ACCOUNT_ID и XE_API_KEY из окружения."""
    aid = os.environ.get("XE_ACCOUNT_ID", "").strip()
    key = os.environ.get("XE_API_KEY", "").strip()
    if not aid or not key:
        raise SystemExit(
            "Задайте переменные окружения XE_ACCOUNT_ID и XE_API_KEY "
            "(https://currencydata.xe.com/account/dashboard)."
        )
    return aid, key


def midmarket_fetch_raw(
    from_currency: str,
    to_currency: str,
    amount: float = 1.0,
    *,
    authorization: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    GET к публичному ``midmarket-converter``: те же query-параметры, что в curl.

    Тело ответа — JSON с полями ``timestamp`` и ``rates``: таблица курсов к доллару США
    (в ответе базовая пара отображается через курс к USD). Явного поля с результатом
    конвертации в JSON нет — итог считается в :func:`midmarket_convert`.

    :param authorization: Значение заголовка ``Authorization`` целиком (включая префикс
        ``Basic ``). По умолчанию — константа с сайта; можно переопределить через
        переменную окружения ``XE_MIDMARKET_AUTHORIZATION``.
    """
    auth = (
        authorization
        or os.environ.get("XE_MIDMARKET_AUTHORIZATION", "").strip()
        or XE_MIDMARKET_AUTHORIZATION_DEFAULT
    )
    params = {
        "from": from_currency.strip().upper(),
        "to": to_currency.strip().upper(),
        "amount": amount,
    }
    q = urllib.parse.urlencode(params, doseq=True)
    url = f"{XE_MIDMARKET_URL.rstrip('/')}/?{q}"

    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": auth,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        data = json.loads(resp.read().decode(charset, errors="replace"))
    if not isinstance(data, dict) or "rates" not in data:
        raise RuntimeError("Неожиданный ответ midmarket-converter (нет rates).")
    return data


def midmarket_convert(
    from_currency: str,
    to_currency: str,
    amount: float = 1.0,
    *,
    authorization: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """
    Конвертация по таблице ``rates`` из midmarket-converter.

    Каждое значение в ``rates[CODE]`` — сколько единиц валюты CODE за **1 USD**.
    Тогда: ``result = amount * rates[to] / rates[from]`` (как пересчёт через USD).

    Возвращает словарь с полями ``from``, ``to``, ``amount``, ``result``, ``timestamp``
    и при необходимости сырые ``rate_from``, ``rate_to`` для проверки.
    """
    payload = midmarket_fetch_raw(
        from_currency,
        to_currency,
        amount,
        authorization=authorization,
        timeout=timeout,
    )
    rates = payload["rates"]
    f = from_currency.strip().upper()
    t = to_currency.strip().upper()
    try:
        r_from = float(rates[f])
        r_to = float(rates[t])
    except (KeyError, TypeError, ValueError) as e:
        raise KeyError(f"Нет курса в ответе для {f} и/или {t}: {e}") from e

    result = float(amount) * (r_to / r_from)
    return {
        "from": f,
        "to": t,
        "amount": float(amount),
        "result": result,
        "timestamp": payload.get("timestamp"),
        "rate_from_per_usd": r_from,
        "rate_to_per_usd": r_to,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Xe: платный convert_from или публичный midmarket-converter"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser(
        "midmarket",
        help="Публичный GET /api/protected/midmarket-converter/ (известный Basic)",
    )
    pm.add_argument("from_ccy", help="ISO 4217, напр. USD")
    pm.add_argument("to_ccy", help="ISO 4217, напр. THB")
    pm.add_argument("amount", type=float, nargs="?", default=1.0)
    pm.add_argument(
        "--raw",
        action="store_true",
        help="Печатать полный JSON ответа (timestamp + все rates), без пересчёта",
    )

    p = sub.add_parser("convert", help="Платный GET /v1/convert_from (нужны ключи)")
    p.add_argument("from_ccy", help="ISO 4217, напр. RUB")
    p.add_argument(
        "to_ccy",
        help="Одна валюта или несколько через запятую, напр. THB или THB,USD",
    )
    p.add_argument("amount", type=float, nargs="?", default=1.0)
    p.add_argument(
        "--interval",
        help="daily | hourly | 15minutes | minutely (зависит от тарифа)",
    )

    args = parser.parse_args()

    try:
        if args.cmd == "midmarket":
            if args.raw:
                data = midmarket_fetch_raw(
                    args.from_ccy, args.to_ccy, args.amount
                )
                print(json.dumps(data, ensure_ascii=False, indent=2))
            else:
                data = midmarket_convert(
                    args.from_ccy, args.to_ccy, args.amount
                )
                print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0

        account_id, api_key = load_credentials()
        to_list: List[str] = [x.strip() for x in args.to_ccy.split(",") if x.strip()]
        extra = {}
        if args.interval:
            extra["interval"] = args.interval

        data = convert_from(
            args.from_ccy,
            to_list,
            args.amount,
            account_id=account_id,
            api_key=api_key,
            **extra,
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"HTTP {e.code}: {body[:2000]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(str(e.reason), file=sys.stderr)
        return 1
    except (KeyError, RuntimeError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
