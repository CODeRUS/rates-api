#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Вывод отчётов по разделам 1–5 (модель UnionPay + MOEX + РСХБ, как в ваших примерах).

Запуск из каталога проекта::

    python fx_reports.py --sections 1
    python fx_reports.py --sections 1,3,5
    python fx_reports.py 2 4
    python fx_reports.py --all
    python fx_reports.py --all --date 2026-03-23 --moex-override 11.812 --thb 30000

Зависит от модулей: ``card_fx_calculator``, ``unionpay_rates`` (и их сетевых запросов).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from typing import Optional, Set

import card_fx_calculator as cfx
import unionpay_rates

@dataclass
class ReportContext:
    """Снимок входных данных для всех разделов."""

    cny_per_thb: float
    moex_cny_rub: float
    rshb_cny_rur_sell: float
    rshb_table_date: date
    rshb_online_table_date: date
    unionpay_cache: dict
    broker_cny_rub: float
    query_date: Optional[date]
    rshb_app_cny_rub: float = 12.08
    atb_cny: float = 12.21
    atb_rub: float = 12.247
    flat_cny: float = 17.0
    flat_rub: float = 199.0
    issuer_atm_cny_pct: float = 0.03
    # Комиссия при снятии рублёвой картой: доля от базы в RUB после CNY→RUB (типично 1%).
    rub_card_atm_pct: float = 0.01
    # True, если входные курсы взяты из файла-кеша после таймаута сети.
    live_inputs_stale: bool = False


def load_context(
    on: Optional[date],
    moex_override: Optional[float],
) -> ReportContext:
    (
        cpt,
        moex,
        sell_dec,
        rshb_d,
        sell_online_dec,
        rshb_online_d,
        stale,
        up,
    ) = cfx.fetch_live_inputs(on, moex_override)
    br = moex
    return ReportContext(
        cny_per_thb=cpt,
        moex_cny_rub=moex,
        rshb_cny_rur_sell=float(sell_dec),
        rshb_table_date=rshb_d,
        rshb_online_table_date=rshb_online_d,
        unionpay_cache=up,
        broker_cny_rub=br,
        query_date=on,
        rshb_app_cny_rub=float(sell_online_dec),
        live_inputs_stale=stale,
    )


def _sep(n: int) -> None:
    print()
    print(f"{'=' * 16} Раздел {n} {'=' * 16}")
    print()


def section2(ctx: ReportContext, atm_fee: float) -> None:
    _sep(2)
    t_cny = cfx.min_thb_for_cny_percent_fee(
        ctx.flat_cny, ctx.issuer_atm_cny_pct, ctx.cny_per_thb, atm_fee
    )
    t_rub = cfx.min_thb_for_rub_percent_fee(
        ctx.flat_rub, 0.01, ctx.cny_per_thb, ctx.rshb_cny_rur_sell, atm_fee
    )
    rub_cny = (t_cny + atm_fee) * ctx.cny_per_thb * (1 + ctx.issuer_atm_cny_pct) * ctx.broker_cny_rub
    rub_rub = (t_rub + atm_fee) * ctx.cny_per_thb * ctx.rshb_cny_rur_sell
    print(
        "Минимальное количество валюты для снятия в банкомате, при котором "
        "достигается «лучший» курс РСХБ (3% CNY / 1% RUB к базе после UnionPay)"
    )
    print(f"(с учётом комиссии банкомата {atm_fee:g} THB)\n")
    print(f"- Юаневая карта (CNY) (Тариф Своя+): {t_cny:.0f} THB ({rub_cny:,.2f} RUB по MOEX)")
    print(f"- Рублёвая карта (RUB): {t_rub:.0f} THB ({rub_rub:,.2f} RUB база до %%)\n")
    print("Если снимать меньший объём, то комиссия РСХБ будет:")
    print(f"- {ctx.flat_cny:g} CNY (Тариф Своя+)")
    print(f"- {ctx.flat_rub:g} RUB (рублёвая карта)")
    print(f"\nДата таблицы РСХБ: {ctx.rshb_table_date.isoformat()} | {cfx._msk_now_str()}")


def section3(ctx: ReportContext) -> None:
    _sep(3)
    print("Курс CNY/RUB\n")
    print(f"• 1 CNY | Мосбиржа (MOEX, CNY/RUB) = {ctx.moex_cny_rub:.3f} RUB")
    print(
        f"• 1 CNY | РСХБ банк (приложение, rates_online) = {ctx.rshb_app_cny_rub:.3f} RUB"
    )
    print(f"• 1 CNY | АТБ CNY = {ctx.atb_cny:.3f} RUB")
    print(f"• 1 CNY | АТБ RUB конвертация рублевых карт = {ctx.atb_rub:.3f} RUB")
    print(
        f"• 1 CNY | РСХБ конвертация рублевых карт (rates_offline, продажа CNY) = "
        f"{ctx.rshb_cny_rur_sell:.3f} RUB"
    )
    ud = ctx.query_date.isoformat() if ctx.query_date else "сегодня (файл UnionPay)"
    print(
        f"\nДата файла UnionPay: {ud} | РСХБ rates_offline: {ctx.rshb_table_date} | "
        f"rates_online: {ctx.rshb_online_table_date}"
    )
    if ctx.live_inputs_stale:
        print(
            f"⚠ Таймаут сети: использованы последние сохранённые курсы "
            f"({cfx.LIVE_INPUTS_CACHE_FILE.name})."
        )


def section4(ctx: ReportContext, thb: float) -> None:
    _sep(4)
    ch = [
        ("Мосбиржа (MOEX CNY/RUB)", ctx.moex_cny_rub),
        ("РСХБ банк (приложение)", ctx.rshb_app_cny_rub),
        (
            "РСХБ конвертация рублевых карт",
            ctx.rshb_cny_rur_sell,
        ),
    ]
    print("Курс THB/RUB (через CNY)")
    print(f"Сумма: {thb:,.2f} THB\n")
    for name, cny_rub in ch:
        total = cfx.payment_rub(thb, ctx.cny_per_thb, cny_rub)
        per = total / thb
        print(f"• {total:,.2f} RUB | {name} (1 THB = {per:.3f} RUB)")
    ud = ctx.query_date.isoformat() if ctx.query_date else "сегодня"
    print(f"\nДата UnionPay JSON: {ud} | РСХБ: {ctx.rshb_table_date} | {cfx._msk_now_str()}")


def section5(ctx: ReportContext, thb: float, atm_fee: float) -> None:
    _sep(5)
    up = ctx.unionpay_cache
    cpt = ctx.cny_per_thb
    pay_cny = thb * cpt
    gross_thb = thb + atm_fee
    base_cny = gross_thb * cpt
    fee_cny = base_cny * ctx.issuer_atm_cny_pct
    tot_cny = base_cny + fee_cny

    def up_get(a: str, b: str) -> float:
        return unionpay_rates.rate_trans_to_base(a, b, cache=up)

    thb_per_cny = up_get("CNY", "THB")
    eur_thb = up_get("EUR", "THB")
    usd_thb = up_get("USD", "THB")

    print(f"{thb:,.2f} THB в юанях:\n")
    print("💳 ОПЛАТА картами UnionPay")
    for label, cny_rub in [
        ("РСХБ CNY (MOEX)", ctx.broker_cny_rub),
        ("РСХБ CNY (UnionPay CNY→THB)", ctx.rshb_app_cny_rub),
        (f"РСХБ RUB ({ctx.rshb_table_date.isoformat()})", ctx.rshb_cny_rur_sell),
    ]:
        rub = cfx.payment_rub(thb, cpt, cny_rub)
        rpt = rub / thb
        print(f"• {pay_cny:,.2f} CNY ({rub:,.2f} RUB, {rpt:.3f} RUB/1 THB) | {label}")

    print()
    print("🏧 СНЯТИЕ в банкомате")
    print(f"(с учётом комиссии банкомата {atm_fee:.2f} THB)\n")

    print(f"• {tot_cny:,.2f} CNY (комиссия: {fee_cny:,.2f} CNY) | РСХБ CNY (MOEX)")
    print(f"• {tot_cny:,.2f} CNY (комиссия: {fee_cny:,.2f} CNY) | РСХБ CNY (UnionPay CNY→THB)")
    print(
        f"• {base_cny:,.2f} CNY (комиссия: 0.00 CNY) | РСХБ RUB ({ctx.rshb_table_date.isoformat()}) "
        f"(счёт в CNY без +3% эмитента; списание в RUB см. ниже)"
    )

    for label, cny_rub in [
        ("РСХБ CNY (MOEX)", ctx.broker_cny_rub),
        ("РСХБ CNY (UnionPay CNY→THB)", ctx.rshb_app_cny_rub),
    ]:
        rub, _ = cfx.atm_rub_from_cny_path(
            thb, atm_fee, cpt, cny_rub, issuer_fee_on_cny_base=ctx.issuer_atm_cny_pct
        )
        comm_rub = fee_cny * cny_rub
        print(f"• {rub:,.2f} RUB (комиссия: {comm_rub:,.2f} RUB) | {label}")

    base_rub_rubcard = base_cny * ctx.rshb_cny_rur_sell
    comm_rub_pct = base_rub_rubcard * ctx.rub_card_atm_pct
    rub_rc, _ = cfx.atm_rub_from_cny_path(
        thb,
        atm_fee,
        cpt,
        ctx.rshb_cny_rur_sell,
        issuer_fee_on_cny_base=0.0,
        extra_rub_pct_of_base=ctx.rub_card_atm_pct,
    )
    print(
        f"• {rub_rc:,.2f} RUB (комиссия: {comm_rub_pct:,.2f} RUB, "
        f"{ctx.rub_card_atm_pct * 100:.0f}% от базы в RUB) | "
        f"РСХБ RUB ({ctx.rshb_table_date.isoformat()})"
    )

    print()
    print(f"Курс UnionPay (файл на дату) | {cfx._msk_now_str()}:")
    print(f"1 THB = {cpt:.3f} CNY")
    print(f"1 THB = {cfx.rub_per_thb(cpt, ctx.broker_cny_rub):.3f} RUB | РСХБ CNY (MOEX)")
    print(f"1 THB = {cfx.rub_per_thb(cpt, ctx.rshb_app_cny_rub):.3f} RUB | РСХБ CNY (приложение)")
    print(f"1 THB = {cfx.rub_per_thb(cpt, ctx.rshb_cny_rur_sell):.3f} RUB | РСХБ RUB")
    print(f"1 CNY = {thb_per_cny:.3f} THB")
    print(f"1 EUR = {eur_thb:.3f} THB")
    print(f"1 USD = {usd_thb:.3f} THB")

    pay_rubc = cfx.payment_rub(thb, cpt, ctx.rshb_cny_rur_sell)
    rub_br, _ = cfx.atm_rub_from_cny_path(
        thb, atm_fee, cpt, ctx.broker_cny_rub, issuer_fee_on_cny_base=ctx.issuer_atm_cny_pct
    )
    rub_app, _ = cfx.atm_rub_from_cny_path(
        thb, atm_fee, cpt, ctx.rshb_app_cny_rub, issuer_fee_on_cny_base=ctx.issuer_atm_cny_pct
    )
    print()
    print("RUB (оценка по курсу карточных операций):")
    print(f"• Оплата: {pay_rubc:,.2f} RUB")
    print(f"• Снятие РСХБ CNY (MOEX): {rub_br:,.2f} RUB")
    print(f"• Снятие РСХБ CNY: {rub_app:,.2f} RUB")
    print(f"• Снятие РСХБ RUB: {rub_rc:,.2f} RUB")


def parse_sections(s: str) -> Set[int]:
    out: Set[int] = set()
    for part in s.replace(" ", "").split(","):
        if not part:
            continue
        n = int(part)
        if n < 1 or n > 5:
            raise ValueError(f"Номер раздела должен быть 1–5, получено: {n}")
        out.add(n)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Отчёты UnionPay+MOEX+РСХБ по разделам 1–5",
    )
    p.add_argument(
        "--sections",
        default=None,
        help="Номера через запятую, например 1,3,5",
    )
    p.add_argument("--all", action="store_true", help="Вывести разделы 1–5 подряд")
    p.add_argument("--date", default=None, help="YYYY-MM-DD (UnionPay JSON)")
    p.add_argument("--moex-override", type=float, default=None)
    p.add_argument("--thb", type=float, default=30_000.0)
    p.add_argument("--atm-fee", type=float, default=250.0)
    p.add_argument(
        "section_nums",
        nargs="*",
        type=int,
        metavar="N",
        help="Номера разделов 1–5 (альтернатива --sections / --all), напр.: 1 3 5",
    )
    args = p.parse_args()

    if args.all:
        wanted = {1, 2, 3, 4, 5}
    elif args.sections:
        wanted = parse_sections(args.sections)
    elif args.section_nums:
        wanted = set()
        for x in args.section_nums:
            if x < 1 or x > 5:
                p.error(f"Раздел должен быть 1–5, получено: {x}")
            wanted.add(x)
    else:
        p.error("Укажите --sections 1,2,... или --all или номера: 1 3 5")

    on = date.fromisoformat(args.date) if args.date else None
    ctx = load_context(on, args.moex_override)

    # Синхронизировать section1 с тем же MOEX/датой: report_example1 заново дергает сеть;
    # передаём moex_override явно.
    for n in sorted(wanted):
        if n == 1:
            _sep(1)
            cfx.report_example1(
                thb_net=args.thb,
                atm_fee_thb=args.atm_fee,
                on=on,
                moex_override=args.moex_override,
            )
        elif n == 2:
            section2(ctx, args.atm_fee)
        elif n == 3:
            section3(ctx)
        elif n == 4:
            section4(ctx, args.thb)
        elif n == 5:
            section5(ctx, args.thb, args.atm_fee)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
