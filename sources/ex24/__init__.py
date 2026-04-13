# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "ex24"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_FIAT_CASH_CATEGORY = {
    "RUB": SourceCategory.CASH_RUB,
    "USD": SourceCategory.CASH_USD,
    "EUR": SourceCategory.CASH_EUR,
    "CNY": SourceCategory.CASH_CNY,
}


def help_text() -> str:
    return "ex24.pro RUB→THB. Полные опции: ex24 --help"


def command(argv: list[str]) -> int:
    from .ex24_rub_thb import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import ex24_rub_thb as e24

    # Одна загрузка главной на вызов: раньше были try_fetch + load — два полных HTTP подряд
    # (удвоение таймаутов и ретраев, «висит» дольше всех в пуле сводки).
    text = e24.load_ex24_main_html()
    rr = (
        e24.real_rate_rub_thb_from_html(text)
        if text
        else None
    )
    rr = rr if rr is not None else e24.DEFAULT_REAL_RATE
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and ctx.receiving_thb > 0)
        else None
    )
    rub_best = float(e24.RUB_MIN_FOR_ZERO_MARKUP)
    if target_thb is not None:
        rub_best = max(1.0, target_thb * rr)
        # Небольшая фикс-точка: курс зависит от amount через markup, сходится за 2-3 итерации.
        for _ in range(4):
            rub_best = max(1.0, target_thb * e24.customer_rate_rub_per_thb(rub_best, rr))
    r_ex = e24.customer_rate_rub_per_thb(rub_best, rr)
    out: List[SourceQuote] = [
        SourceQuote(
            r_ex,
            "Ex24",
            note=(
                f"≈ {fmt_money_ru(target_thb)} THB"
                if target_thb is not None
                else f"от {fmt_money_ru(rub_best)} RUB"
            ),
        )
    ]
    if text:
        for fiat in e24.FIAT_CASH_ORDER:
            thb_per = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(text, fiat)
            cat = _FIAT_CASH_CATEGORY.get(fiat)
            if thb_per is None or cat is None or thb_per <= 0:
                continue
            if fiat == "RUB":
                rate = 1.0 / thb_per
                note = ""
                compare = True
            else:
                rate = thb_per
                note = ""
                compare = False
            out.append(
                SourceQuote(
                    rate,
                    "Ex24",
                    note=note,
                    category=cat,
                    emoji="•",
                    compare_to_baseline=compare,
                )
            )
    return out
