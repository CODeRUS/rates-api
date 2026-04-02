# -*- coding: utf-8 -*-
"""
Bybit P2P (минимальный RUB/USDT среди cash 18 и перевода 14 без 18) + NovaWallet THB/USDT.

Две строки сводки:
  * ``Bybit P2P → NovaWallet`` — ``P_min / R``;
  * ``Bybit P2P → NovaWallet cash (20 000 THB)`` — с доп. USD: cashout fee + 10 THB / R.
"""
from __future__ import annotations

import sys
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

from ..bybit_bitkub import bybit_p2p_usdt_rub as bp
from .novawallet_api import fetch_cashout_fee_usd, fetch_thb_per_usdt

SOURCE_ID = "bybit_novawallet"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_LABEL_PLAIN = "Bybit P2P → NovaWallet"
_LABEL_CASH_20K = "Bybit P2P → NovaWallet cash (20 000 THB)"

_CASH_THB = 20_000.0
_EXTRA_THB = 10.0
_FALLBACK_CASHOUT_USD = 1.5
_MIN_COMPLETION = 99.0


def help_text() -> str:
    return (
        "Bybit P2P (мин. цена среди cash deposit 18 и bank transfer 14 без 18) "
        "+ курс NovaWallet THB/USDT (api.novawallet.org).\n"
        "Используется в общей сводке ``rates.py`` / ``summary``."
    )


def command(argv: list[str]) -> int:
    if argv and argv[0] in ("--help", "-h"):
        print(help_text())
        return 0
    print(help_text(), file=sys.stderr)
    print("Подключается автоматически в сводке; отдельных подкоманд нет.", file=sys.stderr)
    return 0


def _min_bybit_rub_per_usdt(items: list) -> Optional[float]:
    a = bp.filter_cash_deposit_to_bank(items, _MIN_COMPLETION)
    b = bp.filter_bank_transfer_no_cash(items, _MIN_COMPLETION)
    best = bp.min_by_price(a + b)
    if not best:
        return None
    try:
        p = float(best.get("price"))
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    items = bp.fetch_all_online_items(size=20, verification_filter=0)
    items = bp.filter_by_target_usdt(items, target_usdt=bp.DEFAULT_TARGET_USDT)
    p_min = _min_bybit_rub_per_usdt(items)
    if p_min is None:
        ctx.warnings.append(
            "Bybit→NovaWallet: нет объявлений (18 или 14 без 18) с completion≥99 "
            f"({bp.DEFAULT_TARGET_USDT:g} USDT, minAmount≥{bp.DEFAULT_TARGET_USDT:g}·price)"
        )
        return None

    r_thb, w_rate = fetch_thb_per_usdt()
    if w_rate:
        ctx.warnings.append(w_rate)
    if r_thb is None or r_thb <= 0:
        return None

    fee_usd, w_fee = fetch_cashout_fee_usd()
    if fee_usd is None or fee_usd < 0:
        ctx.warnings.append(
            (w_fee or "NovaWallet ledger: нет cashout")
            + f" — для cash-строки взят fallback {_FALLBACK_CASHOUT_USD:g} USD"
        )
        f_usd = _FALLBACK_CASHOUT_USD
    else:
        f_usd = float(fee_usd)

    rub_plain = p_min / r_thb
    usd_for_20k = _CASH_THB / r_thb + f_usd + _EXTRA_THB / r_thb
    rub_cash = (usd_for_20k * p_min) / _CASH_THB

    return [
        SourceQuote(rub_plain, _LABEL_PLAIN),
        SourceQuote(rub_cash, _LABEL_CASH_20K),
    ]
