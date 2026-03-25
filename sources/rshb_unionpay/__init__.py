# -*- coding: utf-8 -*-
"""Плагин rshb_unionpay: UnionPay + MOEX + РСХБ + отчёты (бывшие корневые модули)."""
from __future__ import annotations

# Константы до импорта rates_sources — чтобы при циклическом импорте уже были SOURCE_ID и т.д.
SOURCE_ID = "rshb_unionpay"
EMOJI = "💳"
IS_BASELINE = False

import sys
from datetime import date
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote

CATEGORY = SourceCategory.TRANSFER

_SUBCOMMANDS = (
    ("cardfx", "Калькулятор THB/RUB/CNY (бывший card_fx_calculator.py)"),
    ("unionpay", "UnionPay daily JSON (unionpay_rates.py)"),
    ("moex", "CNY/RUB с MOEX одной строкой (moex_fx.py)"),
    ("rshb-offline", "РСХБ offline HTML (rshb_offline_rates.py)"),
    ("rshb-online", "РСХБ online HTML (rshb_online_rates.py)"),
    ("reports", "Отчёты разделы 1–5 (fx_reports.py)"),
)


def help_text() -> str:
    lines = [
        "РСХБ / UnionPay / MOEX: сводка через card_fx_calculator.",
        "Подкоманды CLI (полные опции: rshb_unionpay <подкоманда> --help):",
    ]
    for name, desc in _SUBCOMMANDS:
        lines.append(f"  {name:12} {desc}")
    return "\n".join(lines)


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print(
            "\nПример: rates.py rshb_unionpay cardfx --date 2026-03-20",
            file=sys.stderr,
        )
        return 0
    head, *tail = argv
    if head == "cardfx":
        from .card_fx_calculator import cli_main

        return cli_main(tail)
    if head == "unionpay":
        from .unionpay_rates import cli_main

        return cli_main(tail)
    if head == "moex":
        from .moex_fx import cli_main

        return cli_main(tail)
    if head == "rshb-offline":
        from .rshb_offline_rates import cli_main

        return cli_main(tail)
    if head == "rshb-online":
        from .rshb_online_rates import cli_main

        return cli_main(tail)
    if head == "reports":
        from .fx_reports import cli_main

        return cli_main(tail)
    print(f"Неизвестная подкоманда {head!r}. {help_text()}", file=sys.stderr)
    return 2


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from . import card_fx_calculator as cfx

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
                emoji="🏧",
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
                    emoji="🏧",
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
                emoji="🏧",
            )
        )

    if live_stale:
        ctx.warnings.append(
            "РСХБ/UnionPay/MOEX: таймаут сети — в расчётах использованы "
            f"последние сохранённые курсы ({cfx.LIVE_INPUTS_CACHE_FILE.name})."
        )
    return out or None
