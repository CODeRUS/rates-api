# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

SOURCE_ID = "rshb_unionpay"
EMOJI = "💳"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER


def help_text() -> str:
    return "РСХБ UnionPay/MOEX: CNY-путь и сценарии снятия (card_fx_calculator)."


def command(argv: list[str]) -> int:
    if not argv or "--help" in argv or "-h" in argv:
        print(help_text())
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import card_fx_calculator as cfx

    on = date.fromisoformat(ctx.unionpay_date) if ctx.unionpay_date else None
    (
        cpt,
        moex,
        sell_dec,
        _,
        sell_online_dec,
        _,
        live_stale,
        _,
    ) = cfx.fetch_live_inputs(on, ctx.moex_override)
    rshb_sell = float(sell_dec)
    rshb_app = float(sell_online_dec)
    broker_cny_rub = float(moex) if moex else 0.0

    out: List[SourceQuote] = []
    if cpt > 0 and broker_cny_rub > 0:
        out.append(
            SourceQuote(cfx.rub_per_thb(cpt, broker_cny_rub), "РСХБ UP CNY (брокер, оплата)")
        )
    if cpt > 0 and rshb_app > 0:
        out.append(
            SourceQuote(cfx.rub_per_thb(cpt, rshb_app), "РСХБ UP CNY (приложение, оплата)")
        )
    if cpt > 0 and rshb_sell > 0:
        out.append(SourceQuote(cfx.rub_per_thb(cpt, rshb_sell), "РСХБ UP RUB (оплата)"))

    thb_ref, atm_fee = ctx.thb_ref, ctx.atm_fee
    if cpt > 0 and broker_cny_rub > 0:
        _rub_atm, rpt = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            broker_cny_rub,
            issuer_fee_on_cny_base=0.03,
        )
        out.append(
            SourceQuote(
                rpt,
                f"РСХБ UP CNY (брокер, снятие {thb_ref:.0f}+{atm_fee:.0f})",
            )
        )
        if rshb_app > 0:
            _rub2, rpt2 = cfx.atm_rub_from_cny_path(
                thb_ref,
                atm_fee,
                cpt,
                rshb_app,
                issuer_fee_on_cny_base=0.03,
            )
            out.append(
                SourceQuote(
                    rpt2,
                    f"РСХБ UP CNY (приложение, снятие {thb_ref:.0f}+{atm_fee:.0f})",
                )
            )
    if cpt > 0 and rshb_sell > 0:
        _rub_rc, rpt_rc = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            rshb_sell,
            issuer_fee_on_cny_base=0.0,
            extra_rub_pct_of_base=0.01,
        )
        out.append(
            SourceQuote(
                rpt_rc,
                f"РСХБ UP RUB (снятие {thb_ref:.0f}+{atm_fee:.0f})",
            )
        )

    if live_stale:
        ctx.warnings.append(
            "РСХБ/UnionPay/MOEX: таймаут сети — в расчётах использованы "
            f"последние сохранённые курсы ({cfx.LIVE_INPUTS_CACHE_FILE.name})."
        )
    return out or None
