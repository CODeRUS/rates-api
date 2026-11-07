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

from rates_primitives import read_novawallet_bundle

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
    from rates_p2p_rub_summary import (
        combo_p2p_label,
        fetch_summary_p2p_rub_legs_live,
        min_rub_per_usdt,
        read_summary_p2p_rub_legs,
    )

    doc = ctx.unified_doc
    fee_usd: Optional[float] = None
    w_fee = ""
    via_htx_min = False

    if doc is not None:
        legs = read_summary_p2p_rub_legs(doc)
        ctx.warnings.extend(legs.warnings)
        p_min, via_htx_min = min_rub_per_usdt(legs)
        r_thb, fee_usd, w_nw = read_novawallet_bundle(doc)
        ctx.warnings.extend(w_nw)
    else:
        legs = fetch_summary_p2p_rub_legs_live()
        ctx.warnings.extend(legs.warnings)
        p_min, via_htx_min = min_rub_per_usdt(legs)
        r_thb, w_rate = fetch_thb_per_usdt()
        if w_rate:
            ctx.warnings.append(w_rate)
        if r_thb is not None and r_thb > 0:
            fee_usd, w_fee = fetch_cashout_fee_usd()

    if p_min is None:
        ctx.warnings.append(
            "P2P RUB/USDT (Bybit/HTX): нет подходящих объявлений под фильтры сводки"
        )
        return None

    if r_thb is None or r_thb <= 0:
        return None

    if fee_usd is None or fee_usd < 0:
        ctx.warnings.append(
            (w_fee or "NovaWallet ledger: нет cashout")
            + f" — для cash-строки взят fallback {_FALLBACK_CASHOUT_USD:g} USD"
        )
        f_usd = _FALLBACK_CASHOUT_USD
    else:
        f_usd = float(fee_usd)

    rub_plain = p_min / r_thb
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and ctx.receiving_thb > 0)
        else _CASH_THB
    )
    usd_for_target = target_thb / r_thb + f_usd + _EXTRA_THB / r_thb
    rub_cash = (usd_for_target * p_min) / target_thb
    plain_label = combo_p2p_label(via_htx=via_htx_min, suffix="NovaWallet")
    cash_label = combo_p2p_label(via_htx=via_htx_min, suffix="NovaWallet cash (20 000 THB)")
    if target_thb != _CASH_THB:
        cash_label = combo_p2p_label(
            via_htx=via_htx_min,
            suffix=f"NovaWallet cash (≈ {int(target_thb)} THB)",
        )

    return [
        SourceQuote(rub_plain, plain_label),
        SourceQuote(rub_cash, cash_label),
    ]
