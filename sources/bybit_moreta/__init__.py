# -*- coding: utf-8 -*-
"""
Bybit P2P (как у NovaWallet: min среди cash 18 и перевода 14 без 18) + Moreta THB/USDT.

Две строки:
- Moreta QR Business: +1.5% комиссия сверху
- Moreta QR Merchant: +2.5% и +4 THB сверху
"""
from __future__ import annotations

import sys
from typing import List, Optional

from rates_primitives import read_moreta_thb_per_usdt
from rates_sources import FetchContext, SourceCategory, SourceQuote

from ..bybit_bitkub import bybit_p2p_usdt_rub as bp
from .moreta_api import fetch_thb_per_usdt

SOURCE_ID = "bybit_moreta"
EMOJI = "📲"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_LABEL_BUSINESS = "Moreta QR Business"
_LABEL_MERCHANT = "Moreta QR Merchant"

_TRANSFER_FEE_USD = 1.0
_BUSINESS_TARGET_THB = 1_000.0
_MERCHANT_TARGET_THB = 200.0
_MIN_COMPLETION = 99.0
_BUSINESS_COMMISSION_RATE = 0.015
_MERCHANT_COMMISSION_RATE = 0.025
_MERCHANT_EXTRA_THB = 4.0


def help_text() -> str:
    return (
        "Bybit P2P (min cash 18 и bank 14 без 18, completion≥99) + Moreta USD_THB; "
        f"две строки: {_LABEL_BUSINESS} (+1.5%) и {_LABEL_MERCHANT} (+2.5% + 4 THB); "
        f"суммы: {_BUSINESS_TARGET_THB:g} THB для Business и {_MERCHANT_TARGET_THB:g} THB для Merchant; "
        f"{_TRANSFER_FEE_USD:g} USD комиссии за перевод."
    )


def command(argv: list[str]) -> int:
    if argv and argv[0] in ("--help", "-h"):
        print(help_text())
        return 0
    print(help_text(), file=sys.stderr)
    print("Подключается в сводке; отдельных подкоманд нет.", file=sys.stderr)
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
    from rates_p2p_rub_summary import (
        fetch_summary_p2p_rub_legs_live,
        min_rub_per_usdt,
        read_summary_p2p_rub_legs,
    )

    doc = ctx.unified_doc

    if doc is not None:
        legs = read_summary_p2p_rub_legs(doc)
        ctx.warnings.extend(legs.warnings)
        p_min, _via_htx = min_rub_per_usdt(legs)
        r_thb, w_mt = read_moreta_thb_per_usdt(doc)
        ctx.warnings.extend(w_mt)
    else:
        legs = fetch_summary_p2p_rub_legs_live()
        ctx.warnings.extend(legs.warnings)
        p_min, _via_htx = min_rub_per_usdt(legs)
        r_thb, w_rate = fetch_thb_per_usdt()
        if w_rate:
            ctx.warnings.append(w_rate)

    if p_min is None:
        ctx.warnings.append(
            "P2P RUB/USDT (Bybit/HTX): нет подходящих объявлений под фильтры сводки"
        )
        return None

    if r_thb is None or r_thb <= 0:
        return None

    business_target_thb = _BUSINESS_TARGET_THB
    merchant_target_thb = _MERCHANT_TARGET_THB

    business_thb_total = business_target_thb * (1.0 + _BUSINESS_COMMISSION_RATE)
    merchant_thb_total = (
        merchant_target_thb * (1.0 + _MERCHANT_COMMISSION_RATE) + _MERCHANT_EXTRA_THB
    )

    usdt_business = business_thb_total / r_thb + _TRANSFER_FEE_USD
    usdt_merchant = merchant_thb_total / r_thb + _TRANSFER_FEE_USD

    rub_per_thb_business = (usdt_business * p_min) / business_target_thb
    rub_per_thb_merchant = (usdt_merchant * p_min) / merchant_target_thb

    label_business = f"{_LABEL_BUSINESS} (≈ {int(business_target_thb)} THB)"
    label_merchant = f"{_LABEL_MERCHANT} (≈ {int(merchant_target_thb)} THB)"

    return [
        SourceQuote(rub_per_thb_business, label_business),
        SourceQuote(rub_per_thb_merchant, label_merchant),
    ]
