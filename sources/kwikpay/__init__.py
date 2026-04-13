# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "kwikpay"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "KwikPay котировки. Полные опции: kwikpay --help"


def command(argv: list[str]) -> int:
    from .kwikpay_rates import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import kwikpay_rates as kw

    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and ctx.receiving_thb > 0)
        else None
    )
    amount_probe = 30_001
    if target_thb is not None:
        # Грубая оценка RUB/THB для стартового запроса; точное значение вернёт API.
        amount_probe = max(1000, int(round(target_thb * 2.55)))
    try:
        kq = kw.fetch_quotes_for_amounts([amount_probe])
    except RuntimeError as e:
        # KwikPay периодически возвращает пустой snap (currency/fee = None).
        # Не шумим в summary, просто пропускаем источник до восстановления ответа.
        if "Нет блока fee" in str(e):
            return None
        raise
    if not kq:
        return None
    q = kq[0]
    if q.withdraw_thb <= 0:
        return None
    if q.fee_rub != 0:
        ctx.warnings.append(
            f"KwikPay: при amount={amount_probe} комиссия не 0 ({q.fee_rub:g} RUB), курс всё же выведен"
        )
    label_note = (
        f"≈ {int(target_thb)} THB" if target_thb is not None else "от 30001 RUB"
    )
    return [SourceQuote(q.rub_per_thb, f"KwikPay ({label_note})")]
