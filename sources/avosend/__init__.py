# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "avosend"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "Avosend API (comission.php). Полные опции: avosend --help"


def command(argv: list[str]) -> int:
    from .avosend_commission import cli_main

    return cli_main(argv)


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import avosend_commission as av

    amt = ctx.avosend_rub
    note = f"от {fmt_money_ru(amt)} RUB"
    avo_label = f"Avosend получение в Big C (от {fmt_money_ru(amt)} RUB)"

    def rate_mode(mode: av.TransferMode) -> Optional[float]:
        try:
            d = av.fetch_commission(amt, mode)
            fr = float(d.get("from"))
            to = float(d.get("to"))
            if to <= 0:
                return None
            return fr / to
        except Exception as e:
            ctx.warnings.append(f"Avosend {mode.value}: {e}")
            return None

    r_bank = rate_mode(av.TransferMode.BANK_ACCOUNT)
    r_cash = rate_mode(av.TransferMode.CASH)
    if r_bank is None and r_cash is None:
        return None
    if r_bank is not None and r_cash is not None:
        if abs(r_bank - r_cash) <= max(1e-9, 1e-9 * abs(r_bank)):
            return [SourceQuote(r_bank, avo_label)]
        return [
            SourceQuote(r_bank, "Avosend на счёт", note=note),
            SourceQuote(r_cash, "Avosend наличные", note=note),
        ]
    if r_bank is not None:
        return [SourceQuote(r_bank, "Avosend на счёт", note=note)]
    return [SourceQuote(r_cash, "Avosend наличные", note=note)]
