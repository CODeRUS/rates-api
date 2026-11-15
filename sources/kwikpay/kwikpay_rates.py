#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Котировки KwikPay (Таиланд THB): парсинг через официальный фронт (Livewire 3).

Прямой POST на ``/livewire/update`` на сайте не работает (405): актуальный URI
берётся из ``window.livewireScriptConfig.uri`` (например
``/livewire-e3174bb1/update``) вместе с CSRF.

Калькулятор на главной — компонент ``online-calculator``, частично lazy-load.
Надёжная схема: (1) страна THA + валюта THB + ``calculate``; (2) значение поля
``amount`` (строка) + ``calculate``.

По ответу API: ``withdrawAmount`` — THB к зачислению, ``acceptedTransferAmount`` —
итого к оплате в RUB, ``acceptedTotalFee`` — комиссия в RUB. На тестах при
THA+THB значение ``amount`` совпадало с рублёвой суммой к оплате в режиме без
комиссии (например ``amount=30001`` → ``acceptedTransferAmount=30001``,
``acceptedTotalFee=0``). Ниже порога комиссия ненулевая (проверяйте актуальные
условия на kwikpay.ru).
"""

from __future__ import annotations

import argparse
import html
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

from rates_http import urlopen_retriable
from typing import Any, Dict, List, Optional, Sequence, Tuple

HOME_URL = "https://kwikpay.ru/"
# Сводка без суммы «получить N THB»: amount выше порога, где комиссия 0.
_DEFAULT_SUMMARY_RUB = 50_000.0
# Значения для поля amount калькулятора (в тестах — рублёвый ориентир к оплате).
DEFAULT_AMOUNTS = (
    500,
    1000,
    5000,
    10000,
    20000,
    29999,
    30000,
    30001,
    50000,
    100000,
)


@dataclass
class KwikQuote:
    amount_input: float
    withdraw_thb: float
    pay_rub: float
    fee_rub: float
    rub_per_thb: float
    api_rate: float

    def as_row(self) -> Tuple[Any, ...]:
        return (
            self.amount_input,
            round(self.withdraw_thb, 2),
            round(self.pay_rub, 2),
            round(self.fee_rub, 2),
            round(self.rub_per_thb, 6),
            round(self.api_rate, 8),
        )


def _fetch(url: str, *, timeout: float = 45.0) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; kwikpay-rates/1.0)"},
    )
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        return r.read().decode("utf-8", "replace")


def _parse_livewire_config(page: str) -> Tuple[str, str]:
    m = re.search(r"window\.livewireScriptConfig\s*=\s*(\{[^<]+\});", page)
    if not m:
        raise RuntimeError("Не найден window.livewireScriptConfig (Livewire отключён?)")
    cfg = json.loads(m.group(1))
    return str(cfg["uri"]), str(cfg["csrf"])


def _parse_calculator_snapshot(page: str) -> Dict[str, Any]:
    m = re.search(
        r'wire:snapshot="([^"]+)"[^>]+wire:name="online-calculator"',
        page,
    )
    if not m:
        raise RuntimeError('Не найден wire:snapshot для wire:name="online-calculator"')
    return json.loads(html.unescape(m.group(1)))


def _livewire_post(
    update_uri: str,
    csrf: str,
    snapshot: Dict[str, Any],
    updates: Dict[str, Any],
    calls: List[Dict[str, Any]],
    *,
    timeout: float = 45.0,
) -> Dict[str, Any]:
    payload = {
        "_token": csrf,
        "components": [
            {
                "snapshot": json.dumps(snapshot, separators=(",", ":")),
                "updates": updates,
                "calls": calls,
            }
        ],
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        update_uri,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; kwikpay-rates/1.0)",
            "X-Requested-With": "XMLHttpRequest",
            "X-Livewire": "true",
            "Origin": "https://kwikpay.ru",
            "Referer": HOME_URL,
        },
        method="POST",
    )
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def _snapshot_from_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(resp["components"][0]["snapshot"])


def _extract_fee_row(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    fee = snap.get("data", {}).get("fee")
    if not fee or not isinstance(fee, list):
        return None
    for item in fee:
        if isinstance(item, dict) and "withdrawAmount" in item and "rate" in item:
            return item
    return None


def _amount_text(amount: float) -> str:
    # Дробная строка вслед за успешным calculate оставляет fee пустым.
    return str(int(round(float(amount))))


def _quote_from_row(amount_input: float, row: Dict[str, Any]) -> KwikQuote:
    w = float(row["withdrawAmount"])
    pay = float(row["acceptedTransferAmount"])
    fee = float(row["acceptedTotalFee"])
    api_rate = float(row["rate"])
    rub_per = pay / w if w else float("nan")
    return KwikQuote(
        amount_input=amount_input,
        withdraw_thb=w,
        pay_rub=pay,
        fee_rub=fee,
        rub_per_thb=rub_per,
        api_rate=api_rate,
    )


def _open_calculator(
    *,
    country: str,
    currency: str,
    timeout: float,
) -> Tuple[str, str, Dict[str, Any]]:
    page = _fetch(HOME_URL, timeout=timeout)
    update_uri, csrf = _parse_livewire_config(page)
    snap = _parse_calculator_snapshot(page)
    snap = _snapshot_from_response(
        _livewire_post(
            update_uri,
            csrf,
            snap,
            {"country": country, "currency": currency},
            [{"path": "", "method": "calculate", "params": []}],
            timeout=timeout,
        )
    )
    return update_uri, csrf, snap


def _quote_on_snapshot(
    update_uri: str,
    csrf: str,
    snap: Dict[str, Any],
    amount: float,
    *,
    timeout: float,
) -> Tuple[KwikQuote, Dict[str, Any]]:
    """Только ``amount``. Страна и валюта в том же апдейте обнуляют расчёт."""
    text = _amount_text(amount)
    resp = _livewire_post(
        update_uri,
        csrf,
        snap,
        {"amount": text},
        [{"path": "", "method": "calculate", "params": []}],
        timeout=timeout,
    )
    snap = _snapshot_from_response(resp)
    row = _extract_fee_row(snap)
    if not row:
        raise RuntimeError(f"Нет блока fee для amount={text}, data={snap.get('data')}")
    return _quote_from_row(float(text), row), snap


def fetch_quotes_for_amounts(
    amounts: Sequence[float],
    *,
    country: str = "THA",
    currency: str = "THB",
    timeout: float = 45.0,
) -> List[KwikQuote]:
    update_uri, csrf, snap = _open_calculator(
        country=country, currency=currency, timeout=timeout
    )
    out: List[KwikQuote] = []
    for raw in amounts:
        quote, snap = _quote_on_snapshot(
            update_uri, csrf, snap, float(raw), timeout=timeout
        )
        out.append(quote)
    return out


def fetch_summary_quote(
    *,
    amount_rub: float = _DEFAULT_SUMMARY_RUB,
    receiving_thb: Optional[float] = None,
    timeout: float = 45.0,
) -> KwikQuote:
    """
    Одна котировка счёта RUB→THB для сводки.

    При ``receiving_thb`` второй запрос подбирает ``amount`` по курсу пробы.
    """
    update_uri, csrf, snap = _open_calculator(
        country="THA", currency="THB", timeout=timeout
    )
    quote, snap = _quote_on_snapshot(
        update_uri, csrf, snap, float(amount_rub), timeout=timeout
    )
    target = float(receiving_thb) if receiving_thb is not None else 0.0
    if target > 0 and quote.withdraw_thb > 0 and quote.rub_per_thb > 0:
        scaled = max(1000.0, target * quote.rub_per_thb)
        if abs(scaled - quote.amount_input) > 1.0:
            quote, _snap = _quote_on_snapshot(
                update_uri, csrf, snap, scaled, timeout=timeout
            )
    return quote


def _print_table(rows: List[KwikQuote]) -> None:
    hdr = (
        "Ввод amount",
        "THB зачисл.",
        "RUB к оплате",
        "Комиссия RUB",
        "RUB/THB (эфф.)",
        "rate (API)",
    )
    w = [max(len(h), 12) for h in hdr]
    line = " | ".join(h.ljust(w[i]) for i, h in enumerate(hdr))
    print(line)
    print("-" * len(line))
    for r in rows:
        cells = r.as_row()
        print(" | ".join(str(c).ljust(w[i]) for i, c in enumerate(cells)))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="KwikPay: эффективный RUB/THB и комиссия для разных значений поля amount"
    )
    p.add_argument(
        "--amounts",
        type=str,
        default=",".join(str(x) for x in DEFAULT_AMOUNTS),
        help="Список значений amount через запятую (см. модульный docstring)",
    )
    p.add_argument("--country", default="THA")
    p.add_argument("--currency", default="THB")
    p.add_argument("--json", action="store_true", help="Вывод JSON в stdout")
    return p


def cli_main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    amounts = [int(x.strip()) for x in args.amounts.split(",") if x.strip()]
    try:
        quotes = fetch_quotes_for_amounts(
            amounts, country=args.country, currency=args.currency
        )
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read()[:500]!r}", file=sys.stderr)
        return 1
    except Exception as e:
        print(e, file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [q.__dict__ for q in quotes],
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_table(quotes)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
