#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент к публичному API тарифов Korona Pay (переводы RUB → THB и др.).

Базовый URL (как в веб-приложении)::
    GET https://api.koronapay.com/transfers/tariffs

Важно: **суммы в запросе и ответе — в минорных единицах** (копейки для RUB, сатанги для THB).

Порог курса **100 000 RUB** (направление RUS→THA, карта → DeeMoney и т.п. проверено эмпирически):
  * до **99 999,99 RUB** — один уровень ``exchangeRate`` (хуже для отправителя);
  * с **100 000,00 RUB** — другой, выгоднее (ниже RUB за 1 THB при direct).

Без заголовков ``Origin`` / ``Referer`` с домена koronapay.com сервер может отвечать **406/400**.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Union

from rates_http import urlopen_retriable

TARIFFS_URL = "https://api.koronapay.com/transfers/tariffs"

DEFAULT_PARAMS: Dict[str, Union[str, bool]] = {
    "sendingCountryId": "RUS",
    "sendingCurrencyId": "810",
    "receivingCountryId": "THA",
    "receivingCurrencyId": "764",
    "paymentMethod": "debitCard",
    "receivingMethod": "accountViaDeeMoney",
    "paidNotificationEnabled": False,
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Origin": "https://koronapay.com",
    "Referer": "https://koronapay.com/",
}

# Порог в **копейках** (100 000 руб × 100)
RUB_100K_KOPECKS = 10_000_000

# Минимальная сумма **отправки в RUB**, с которой API отдаёт более выгодный ``exchangeRate``
# (ниже RUB за 1 THB при типичном направлении карта → DeeMoney). До 99 999,99 — предыдущий уровень.
RUB_MIN_SENDING_FOR_BEST_TIER = RUB_100K_KOPECKS / 100.0


def rub_to_kopecks(rub: float) -> int:
    """Целые копейки из суммы в рублях (округление к ближайшему центу)."""
    return int(round(float(rub) * 100))


def thb_to_satang(thb: float) -> int:
    """Сатанги из суммы в батах."""
    return int(round(float(thb) * 100))


def kopecks_to_rub(k: int) -> float:
    return k / 100.0


def satang_to_thb(s: int) -> float:
    return s / 100.0


def fetch_tariffs(
    *,
    params: Optional[Dict[str, Any]] = None,
    sending_amount_kopecks: Optional[int] = None,
    receiving_amount_satang: Optional[int] = None,
    timeout: float = 30.0,
    headers: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """
    Запрос тарифа. Укажите **либо** ``sending_amount_kopecks``, **либо**
    ``receiving_amount_satang`` (взаимоисключающие параметры API).

    :return: Список вариантов (обычно один элемент) с полями вроде
             ``sendingAmount``, ``receivingAmount``, ``exchangeRate``, ``exchangeRateType``.
    """
    q: Dict[str, Any] = dict(DEFAULT_PARAMS)
    if params:
        q.update(params)

    if sending_amount_kopecks is not None and receiving_amount_satang is not None:
        raise ValueError("Задайте только sending_amount_kopecks или receiving_amount_satang")

    if sending_amount_kopecks is not None:
        q["sendingAmount"] = int(sending_amount_kopecks)
        q.pop("receivingAmount", None)
    elif receiving_amount_satang is not None:
        q["receivingAmount"] = int(receiving_amount_satang)
        q.pop("sendingAmount", None)
    else:
        raise ValueError("Нужен sending_amount_kopecks или receiving_amount_satang")

    # bool → lowercase json-style для query
    enc: Dict[str, str] = {}
    for k, v in q.items():
        if isinstance(v, bool):
            enc[k] = "true" if v else "false"
        else:
            enc[k] = str(v)

    url = TARIFFS_URL + "?" + urllib.parse.urlencode(enc)
    h = dict(DEFAULT_HEADERS)
    if headers:
        h.update(headers)

    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method="GET", headers=h)
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if isinstance(data, dict) and data.get("type") == "error":
        raise RuntimeError(f"Korona API: {data.get('message', data)}")
    if not isinstance(data, list):
        raise RuntimeError(f"Неожиданный ответ: {type(data)}")
    return data


def summarize_tariff(row: Dict[str, Any]) -> str:
    """Краткая строка для человека: курс и суммы в основных единицах."""
    sa = row.get("sendingAmount")
    ra = row.get("receivingAmount")
    er = row.get("exchangeRate")
    ert = row.get("exchangeRateType")
    if sa is None or ra is None:
        return json.dumps(row, ensure_ascii=False)
    rub = kopecks_to_rub(int(sa))
    thb = satang_to_thb(int(ra))
    return (
        f"Отправка {rub:,.2f} RUB → получение {thb:,.2f} THB | "
        f"exchangeRate={er} ({ert}) | RUB за 1 THB ≈ {rub/thb:.4f}"
    )


def compare_rub_100k_tier(
    *,
    payment_method: str = "debitCard",
    receiving_method: str = "accountViaDeeMoney",
    timeout: float = 30.0,
) -> Dict[str, Any]:
    """
    Сравнивает курс чуть ниже и на пороге 100 000 RUB (типичная смена ``exchangeRate``).
    """
    base_extra = {
        "paymentMethod": payment_method,
        "receivingMethod": receiving_method,
    }
    below = RUB_100K_KOPECKS - 100  # 99 999.00 RUB
    at = RUB_100K_KOPECKS  # 100 000.00 RUB

    a = fetch_tariffs(
        params=base_extra,
        sending_amount_kopecks=below,
        timeout=timeout,
    )[0]
    b = fetch_tariffs(
        params=base_extra,
        sending_amount_kopecks=at,
        timeout=timeout,
    )[0]
    return {
        "below_100k_rub": kopecks_to_rub(below),
        "at_100k_rub": kopecks_to_rub(at),
        "rate_below": a.get("exchangeRate"),
        "rate_at_100k": b.get("exchangeRate"),
        "receiving_thb_below": satang_to_thb(int(a["receivingAmount"])),
        "receiving_thb_at_100k": satang_to_thb(int(b["receivingAmount"])),
        "raw_below": a,
        "raw_at": b,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Korona Pay API — тарифы переводов")
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("compare-100k", help="Сравнить курс до и от 100 000 RUB")
    pc.add_argument("--payment", default="debitCard")
    pc.add_argument("--receiving", default="accountViaDeeMoney")

    pq = sub.add_parser("query", help="Один запрос по сумме отправки или получения")
    g = pq.add_mutually_exclusive_group(required=True)
    g.add_argument("--sending-rub", type=float, help="Сумма отправки в рублях")
    g.add_argument("--receiving-thb", type=float, help="Сумма получения в батах")
    pq.add_argument("--payment", default="debitCard")
    pq.add_argument("--receiving", default="accountViaDeeMoney")
    pq.add_argument(
        "--raw",
        action="store_true",
        help="Печатать полный JSON первого тарифа",
    )
    return p


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        if args.cmd == "compare-100k":
            r = compare_rub_100k_tier(
                payment_method=args.payment,
                receiving_method=args.receiving,
            )
            print("Сравнение порога 100 000 RUB (отправка, копейки в API):\n")
            print(f"  До порога:   {r['below_100k_rub']:,.2f} RUB → rate={r['rate_below']}, THB={r['receiving_thb_below']:,.2f}")
            print(f"  На пороге:  {r['at_100k_rub']:,.2f} RUB → rate={r['rate_at_100k']}, THB={r['receiving_thb_at_100k']:,.2f}")
            print(
                f"\nРазница курсов (поле exchangeRate): {r['rate_below']} → {r['rate_at_100k']} "
                f"(при ``direct`` меньшее значение обычно выгоднее отправителю)"
            )
            return 0

        extra = {"paymentMethod": args.payment, "receivingMethod": args.receiving}
        if args.sending_rub is not None:
            kop = rub_to_kopecks(args.sending_rub)
            rows = fetch_tariffs(
                params=extra,
                sending_amount_kopecks=kop,
            )
        else:
            sat = thb_to_satang(args.receiving_thb)
            rows = fetch_tariffs(
                params=extra,
                receiving_amount_satang=sat,
            )
        if args.raw:
            print(json.dumps(rows[0], ensure_ascii=False, indent=2))
        else:
            print(summarize_tariff(rows[0]))
        return 0
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, ValueError) as e:
        if isinstance(e, urllib.error.HTTPError) and e.fp:
            body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code}: {body[:2000]}", file=sys.stderr)
        else:
            print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
