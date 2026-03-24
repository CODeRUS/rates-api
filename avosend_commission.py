#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент к API расчёта комиссии и курса Avosend (POST form-urlencoded).

Endpoint: https://avosend.com/api/comission.php  (написание как у сервиса)

Сервер может вернуть HTML-обёртку со встроенным JSON; модуль ищет объект,
начинающийся с поля ``fromScale`` (как в фактическом ответе).

Три режима (RU → TH):

1. **Наличными** — ``toPrvId=135461``, ``currencyIdTo=764`` (THB)
2. **На счёт в банке** — ``toPrvId=132953``, ``currencyIdTo=764`` (THB)
3. **На банковскую карту** — ``toPrvId=135526``, ``currencyIdTo=840`` (USD)

Переменная окружения ``AVOSEND_COOKIE`` — опционально, если без сессии ответ пустой/ошибка.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

COMISSION_URL = "https://avosend.com/api/comission.php"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"


class TransferMode(str, Enum):
    """Режим зачисления в Таиланде."""

    CASH = "cash"  # наличными
    BANK_ACCOUNT = "bank"  # на счёт в банке
    CARD = "card"  # на банковскую карту (USD)


@dataclass(frozen=True)
class ModeParams:
    to_prv_id: int
    currency_id_to: int
    description: str


MODE_MAP: Dict[TransferMode, ModeParams] = {
    TransferMode.CASH: ModeParams(135461, 764, "Наличными (THB)"),
    TransferMode.BANK_ACCOUNT: ModeParams(132953, 764, "На счёт в банке (THB)"),
    TransferMode.CARD: ModeParams(135526, 840, "На банковскую карту (USD)"),
}


def _default_form_fields() -> Dict[str, str]:
    return {
        "countryCodeFrom": "ru",
        "countryIdFrom": "643",
        "countryCodeTo": "th",
        "countryIdTo": "764",
        "currencyIdFrom": "643",
        "direction": "from",
    }


def _extract_json_object(text: str) -> Dict[str, Any]:
    """
    Достаёт основной JSON из ответа (часто после HTML/скриптов).
    """
    text = text.strip()
    # Чистый JSON
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    m = re.search(r"\{\s*\"fromScale\"\s*:", text)
    if not m:
        raise ValueError(
            "В ответе не найден JSON с полем fromScale. "
            "Первые 200 символов:\n" + text[:200]
        )
    obj, _ = json.JSONDecoder().raw_decode(text[m.start() :])
    if not isinstance(obj, dict):
        raise ValueError("Распарсенное значение не объект")
    return obj


def fetch_commission(
    summ_send: float,
    mode: TransferMode,
    *,
    direction: str = "from",
    timeout: float = 30.0,
    cookie: Optional[str] = None,
    extra_fields: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Запрос пересчёта: сумма отправки, курс, комиссия.

    :param summ_send: сумма в валюте отправки (RUB), как в примерах API.
    :param mode: один из трёх режимов :class:`TransferMode`.
    :param direction: ``from`` — как в ваших примерах.
    """
    mp = MODE_MAP[mode]
    fields = _default_form_fields()
    fields["summSend"] = str(summ_send)
    fields["direction"] = direction
    fields["toPrvId"] = str(mp.to_prv_id)
    fields["currencyIdTo"] = str(mp.currency_id_to)
    if extra_fields:
        fields.update(extra_fields)

    body = urllib.parse.urlencode(fields).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": USER_AGENT,
        "Referer": "https://avosend.com/",
        "Origin": "https://avosend.com",
        "Accept": "*/*",
    }
    ck = cookie if cookie is not None else os.environ.get("AVOSEND_COOKIE", "").strip()
    if ck:
        headers["Cookie"] = ck

    ctx = ssl.create_default_context()
    req = urllib.request.Request(COMISSION_URL, data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")

    data = _extract_json_object(raw)
    code = data.get("code")
    if code not in (0, None) and code != "0":
        err = data.get("errorMessage") or data.get("message") or data
        raise RuntimeError(f"Avosend API code={code}: {err}")
    return data


def format_summary(data: Dict[str, Any]) -> str:
    """Краткая строка для человека."""
    parts = [
        f"Отправка: {data.get('from')} RUB",
        f"К получению: {data.get('to')} (валюта по currencyIdTo)",
        f"Комиссия: {data.get('fee')}",
        f"Курс (convertRate): {data.get('convertRate')}",
        f"Текст курса: {data.get('currencyRateText')}",
    ]
    if data.get("tariffs"):
        parts.append(f"Тарифы: {data['tariffs']}")
    return " | ".join(str(p) for p in parts)


def _main() -> int:
    p = argparse.ArgumentParser(description="Avosend API — comission.php")
    p.add_argument(
        "mode",
        choices=["cash", "bank", "card"],
        help="cash=наличные THB, bank=счёт THB, card=карта USD",
    )
    p.add_argument("amount", type=float, help="Сумма отправки (RUB), например 7000")
    p.add_argument("--json", action="store_true", help="Печатать полный JSON")
    p.add_argument("--raw", action="store_true", help="Сырой ответ сервера в stdout")
    args = p.parse_args()

    mode = TransferMode(args.mode)
    try:
        if args.raw:
            fields = _default_form_fields()
            mp = MODE_MAP[mode]
            fields.update(
                {
                    "summSend": str(args.amount),
                    "direction": "from",
                    "toPrvId": str(mp.to_prv_id),
                    "currencyIdTo": str(mp.currency_id_to),
                }
            )
            body = urllib.parse.urlencode(fields).encode()
            req = urllib.request.Request(
                COMISSION_URL,
                data=body,
                method="POST",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "User-Agent": USER_AGENT,
                    "Referer": "https://avosend.com/",
                    "Origin": "https://avosend.com",
                },
            )
            ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                sys.stdout.buffer.write(r.read())
            return 0

        data = fetch_commission(args.amount, mode)
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(format_summary(data))
        return 0
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, RuntimeError) as e:
        if isinstance(e, urllib.error.HTTPError) and e.fp:
            print(e.read().decode("utf-8", errors="replace")[:3000], file=sys.stderr)
        else:
            print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
