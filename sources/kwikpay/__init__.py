# -*- coding: utf-8 -*-
"""KwikPay: калькулятор kwikpay.ru (счёт RUB→THB), категория TRANSFER."""
from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from rates_categories import SourceCategory

if TYPE_CHECKING:
    from rates_sources import FetchContext, SourceQuote

SOURCE_ID = "kwikpay"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return (
        "KwikPay (kwikpay.ru): счёт RUB→THB в сводке, без токена мобильного API. "
        "Полные опции: kwikpay --help"
    )


def command(argv: list[str]) -> int:
    from .kwikpay_rates import cli_main

    return cli_main(argv)


def summary(ctx: "FetchContext") -> Optional[List["SourceQuote"]]:
    from rates_sources import SourceQuote, fmt_money_ru

    from .kwikpay_rates import fetch_summary_quote

    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and float(ctx.receiving_thb) > 0)
        else None
    )
    try:
        quote = fetch_summary_quote(receiving_thb=target_thb)
    except Exception as e:
        ctx.warnings.append(f"KwikPay: {e}")
        return None
    rate = quote.rub_per_thb
    if rate != rate or rate <= 0:  # NaN
        ctx.warnings.append("KwikPay: пустой курс с сайта")
        return None
    note = (
        f"≈ {fmt_money_ru(target_thb)} THB, счёт"
        if target_thb is not None
        else f"от {fmt_money_ru(quote.pay_rub)} RUB, счёт"
    )
    if quote.fee_rub:
        note += f", ком. {fmt_money_ru(quote.fee_rub)} RUB"
    return [
        SourceQuote(
            rate,
            "KwikPay счёт",
            note=note,
            category=CATEGORY,
            emoji=EMOJI,
        )
    ]
