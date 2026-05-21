# -*- coding: utf-8 -*-
"""KwikPay: mob.kwikpay.ru commissions API (счёт THB + карта USD), категория TRANSFER."""
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
        "KwikPay (mob.kwikpay.ru): счёт RUB→THB; карта RUB→USD×BBL TT → RUB/THB в сводке. "
        "Нужны KWIKPAY_AUTH_TOKEN и BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY (карта). "
        "Полные опции: kwikpay --help"
    )


def command(argv: list[str]) -> int:
    from .kwikpay_rates import cli_main

    return cli_main(argv)


def summary(ctx: "FetchContext") -> Optional[List["SourceQuote"]]:
    import urllib.error

    from rates_sources import SourceQuote, fmt_money_ru
    from sources.unired_bkb import bbl_latest_fx as bbl

    from . import kwikpay_mob as mob

    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and float(ctx.receiving_thb) > 0)
        else None
    )
    try:
        fees = mob.fetch_summary_fees(receiving_thb=target_thb)
    except RuntimeError as e:
        ctx.warnings.append(f"KwikPay: {e}")
        return None

    thb_per_usd: Optional[float] = None
    if any(f.operation_type == "VisaDirect" for f in fees):
        if not bbl.subscription_key_from_env():
            ctx.warnings.append(
                "KwikPay карта: задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY для USD/THB (как Unired)"
            )
        else:
            try:
                thb_per_usd = bbl.fetch_usd50_tt_thb()
            except (RuntimeError, OSError, urllib.error.URLError, ValueError) as e:
                ctx.warnings.append(f"KwikPay карта × Bangkok Bank: {e}")
            except Exception as e:
                ctx.warnings.append(f"KwikPay карта × Bangkok Bank: {e}")

    out: List[SourceQuote] = []
    for fee in fees:
        if fee.operation_type == "OverseasDeposits":
            rt = fee.rub_per_thb
            if rt is None or rt <= 0:
                continue
            note = (
                f"≈ {fmt_money_ru(target_thb)} THB, счёт"
                if target_thb is not None
                else f"от {fmt_money_ru(fee.accepted_transfer_rub)} RUB, счёт"
            )
            if fee.fee_rub:
                note += f", ком. {fmt_money_ru(fee.fee_rub)} RUB"
            out.append(
                SourceQuote(
                    rt,
                    "KwikPay счёт",
                    note=note,
                    category=CATEGORY,
                    emoji=EMOJI,
                )
            )
        elif fee.operation_type == "VisaDirect":
            if thb_per_usd is None or thb_per_usd <= 0:
                continue
            rt = fee.rub_per_thb_via_usd(thb_per_usd)
            if rt is None or rt <= 0:
                continue
            note = f"≈ {fmt_money_ru(fee.withdraw_amount)} USD, карта"
            if fee.fee_rub:
                note += f", ком. {fmt_money_ru(fee.fee_rub)} RUB"
            out.append(
                SourceQuote(
                    rt,
                    "KwikPay карта",
                    note=note,
                    category=CATEGORY,
                    emoji=EMOJI,
                )
            )
    return out or None
