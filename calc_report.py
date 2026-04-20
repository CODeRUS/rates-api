#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сравнение каналов RUB→THB для фиксированного бюджета (команда calc).
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_FIAT_ALIASES = frozenset({"usd", "eur", "cny"})

_ASKMONEY_RUB_THB_MODULE: Any = None


def _askmoney_rub_thb_module() -> Any:
    """
    Загрузка ``askmoney_rub_thb.py`` без импорта ``sources.askmoney`` (цикл с rates_sources).
    """
    global _ASKMONEY_RUB_THB_MODULE
    if _ASKMONEY_RUB_THB_MODULE is None:
        path = _ROOT / "sources" / "askmoney" / "askmoney_rub_thb.py"
        name = "calc_askmoney_rub_thb"
        spec = importlib.util.spec_from_file_location(name, str(path))
        if spec is None or spec.loader is None:
            raise ImportError("askmoney_rub_thb")
        mod = importlib.util.module_from_spec(spec)
        # До exec_module обязателен подстановка в sys.modules: иначе на Python 3.9
        # dataclasses ломаются (cls.__module__ не найден → None.__dict__).
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        _ASKMONEY_RUB_THB_MODULE = mod
    return _ASKMONEY_RUB_THB_MODULE


@dataclass(frozen=True)
class _CalcRowOut:
    label: str
    thb: float
    rate_rub_per_thb: float


def parse_calc_cli_argv(argv: Sequence[str]) -> Tuple[float, str, float]:
    if len(argv) < 3:
        raise ValueError(
            "Укажите: RUB валюта курс — например: calc 100000 usd 83\n"
            "валюта: usd | eur | cny; курс — рублей за 1 ед. валюты."
        )
    try:
        rub = float(str(argv[0]).replace(",", "."))
    except ValueError as e:
        raise ValueError("Первый аргумент должен быть суммой RUB.") from e
    fiat = str(argv[1]).strip().lower()
    try:
        rate = float(str(argv[2]).replace(",", "."))
    except ValueError as e:
        raise ValueError("Третий аргумент должен быть курсом ₽/ед. валюты.") from e
    if rub <= 0:
        raise ValueError("Сумма RUB должна быть больше 0.")
    if rate <= 0:
        raise ValueError("Курс должен быть больше 0.")
    if fiat not in _FIAT_ALIASES:
        raise ValueError("Валюта должна быть usd, eur или cny.")
    return rub, fiat, rate


def calc_subcommand_help() -> str:
    return (
        "calc — сравнение RUB→THB по каналам при фиксированном бюджете.\n"
        "  calc RUB usd|eur|cny КУРС\n"
        "  КУРС — сколько ₽ за 1 ед. валюты (ваша покупка валюты для сценария TT Exchange).\n"
        "Пример: calc 100000 usd 83\n"
        "Общие опции rates.py (--refresh) действуют на чтение кешей TT/unified."
    )


def _calc_table_lines(
    rows: List[_CalcRowOut],
    *,
    best_thb: float,
    budget_rub: float,
) -> List[str]:
    """
    Таблица: между колонками ровно два пробела; числа вправо, «Канал» влево.
    """
    headers = ("#", "Курс", "THB", "Δ THB", "Δ RUB", "Канал")
    data_rows: List[Tuple[str, str, str, str, str, str]] = []
    for i, r in enumerate(rows, start=1):
        dth = best_thb - r.thb
        drub = best_thb * (budget_rub / r.thb) - budget_rub if r.thb > 0 else 0.0
        data_rows.append(
            (
                str(i),
                f"{r.rate_rub_per_thb:.3f}",
                f"{r.thb:.2f}",
                f"{dth:.2f}",
                f"{drub:.0f}",
                r.label,
            )
        )

    widths = [len(h) for h in headers]
    for row in data_rows:
        for j in range(5):
            widths[j] = max(widths[j], len(row[j]))

    sep = "  "

    def _fmt_row(cells: Tuple[str, ...]) -> str:
        left = sep.join(cells[j].rjust(widths[j]) for j in range(5))
        return f"{left}{sep}{cells[5]}"

    out = [_fmt_row(headers)]
    for row in data_rows:
        out.append(_fmt_row(row))
    return out


def build_calc_report_text(
    *,
    budget_rub: float,
    fiat_code: str,
    rub_per_fiat_unit: float,
    atm_fee_thb: float = 250.0,
    unionpay_on: Optional[date] = None,
    moex_override: Optional[float] = None,
    lang: str = "ru",
    timeout: float = 28.0,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    readonly: bool = False,
) -> Tuple[str, List[str]]:
    from exchange_report import best_fiat_buy_thb_across_branches
    from sources.avosend import avosend_commission as av
    from sources.rshb_unionpay.card_fx_calculator import (
        _msk_now_str,
        fetch_live_inputs,
        max_thb_net_for_atm_rub_budget,
    )

    fiat = fiat_code.strip().lower()
    fiat_tt = fiat.upper()
    warnings: List[str] = []
    rows: List[_CalcRowOut] = []

    cpt, moex, rshb_sell_dec, _rshb_date, online_sell_dec, _, rshb_stale, _ = (
        fetch_live_inputs(unionpay_on, moex_override, readonly=readonly)
    )
    if rshb_stale and not readonly:
        warnings.append("РСХБ/UnionPay: использованы сохранённые курсы (таймаут сети).")

    rshb_sell = float(rshb_sell_dec)
    online_sell_f = float(online_sell_dec)

    thb_broker = max_thb_net_for_atm_rub_budget(
        budget_rub,
        cny_per_thb=cpt,
        atm_fee_thb=atm_fee_thb,
        cny_rub=float(moex),
        rub_card=False,
    )
    thb_app = max_thb_net_for_atm_rub_budget(
        budget_rub,
        cny_per_thb=cpt,
        atm_fee_thb=atm_fee_thb,
        cny_rub=online_sell_f,
        rub_card=False,
    )
    thb_rubcard = max_thb_net_for_atm_rub_budget(
        budget_rub,
        cny_per_thb=cpt,
        atm_fee_thb=atm_fee_thb,
        cny_rub=rshb_sell,
        rub_card=True,
    )
    if thb_broker > 0:
        rows.append(
            _CalcRowOut(
                "РСХБ UP CNY брокер",
                thb_broker,
                budget_rub / thb_broker,
            )
        )
    if thb_app > 0:
        rows.append(
            _CalcRowOut(
                "РСХБ UP CNY приложение",
                thb_app,
                budget_rub / thb_app,
            )
        )
    if thb_rubcard > 0:
        rows.append(
            _CalcRowOut(
                "РСХБ, рублёвая карта",
                thb_rubcard,
                budget_rub / thb_rubcard,
            )
        )

    try:
        am = _askmoney_rub_thb_module()
        if readonly:
            am_params = am.load_params(fetch=False, html_file=None)
        else:
            am_params = am.load_params(fetch=True, html_file=None)
        am_thb_i = am.rub_to_thb(budget_rub, am_params)
        am_thb = float(am_thb_i)
        if am_thb > 0:
            rows.append(_CalcRowOut("AskMoney", am_thb, budget_rub / am_thb))
    except Exception as e:
        warnings.append(f"AskMoney: {e}")

    try:
        avo = av.fetch_commission(float(budget_rub), av.TransferMode.CASH)
        avo_thb = float(avo.get("to") or 0.0)
        if avo_thb > 0:
            rows.append(
                _CalcRowOut("Avosend получение в Big C", avo_thb, budget_rub / avo_thb)
            )
    except Exception as e:
        warnings.append(f"Avosend Big C: {e}")

    best_tt, tt_warn = best_fiat_buy_thb_across_branches(
        fiat_code=fiat_tt,
        lang=lang,
        timeout=timeout,
        parallel_max_workers=parallel_max_workers,
        refresh=refresh,
        readonly=readonly,
    )
    warnings.extend(tt_warn)
    if best_tt is not None and best_tt > 0:
        fiat_amt = budget_rub / rub_per_fiat_unit
        tt_thb = fiat_amt * best_tt
        if tt_thb > 0:
            label = f"TT Exchange {fiat_tt}"
            rows.append(_CalcRowOut(label, tt_thb, budget_rub / tt_thb))

    rows.sort(key=lambda r: r.thb, reverse=True)
    if not rows:
        body = (
            f"Сравнение RUB→THB, бюджет {budget_rub:,.0f} ₽\n"
            f"{_msk_now_str()}\n\n"
            "(нет ни одной строки с положительным THB)\n"
        )
        return body, warnings

    best_thb = rows[0].thb
    lines: List[str] = [
        f"Сравнение RUB→THB · бюджет {budget_rub:,.0f} ₽ · TT: {fiat_tt} по {rub_per_fiat_unit:g} ₽/ед.",
        _msk_now_str(),
        "",
        "```",
    ]
    lines.extend(
        _calc_table_lines(rows, best_thb=best_thb, budget_rub=budget_rub)
    )
    lines.append("```")
    lines.extend(
        [
            "",
            "Курс — рублей за 1 THB при вашем бюджете.",
            "Δ THB — на сколько бат меньше, чем у лучшего варианта.",
            "Δ RUB — сколько дополнительных рублей нужно тем же способом, чтобы получить столько THB, сколько у лучшего.",
        ]
    )

    return "\n".join(lines) + "\n", warnings


def _parse_calc_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("--atm-fee", type=float, default=250.0, help="Комиссия банкомата THB (РСХБ)")
    p.add_argument("--lang", type=str, default="ru", help="Язык списка филиалов TT")
    p.add_argument("--timeout", type=float, default=28.0, help="Таймаут HTTP")
    p.add_argument("--refresh", action="store_true", help="Обновить L1 TT при расчёте")
    p.add_argument(
        "--readonly",
        action="store_true",
        help="Только кеш UnionPay/РСХБ (.card_fx_live_inputs_cache) и L1 TT, без сети",
    )
    p.add_argument("--unionpay-date", default=None, help="YYYY-MM-DD (UnionPay / РСХБ)")
    p.add_argument("--moex-override", type=float, default=None)
    p.add_argument("rub", type=str, nargs="?")
    p.add_argument("fiat", type=str, nargs="?")
    p.add_argument("fx", type=str, nargs="?")
    return p.parse_args(argv)


def main_calc_cli(argv: List[str]) -> int:
    args = _parse_calc_argv(argv)
    if args.help:
        print(calc_subcommand_help())
        return 0
    if args.rub is None or args.fiat is None or args.fx is None:
        print(calc_subcommand_help(), file=sys.stderr)
        return 2
    try:
        br, fiat, rp = parse_calc_cli_argv([args.rub, args.fiat, args.fx])
    except (TypeError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    on = date.fromisoformat(args.unionpay_date) if args.unionpay_date else None
    if args.atm_fee <= 0:
        print("--atm-fee должен быть > 0", file=sys.stderr)
        return 2
    try:
        text, w = build_calc_report_text(
            budget_rub=br,
            fiat_code=fiat,
            rub_per_fiat_unit=rp,
            atm_fee_thb=float(args.atm_fee),
            unionpay_on=on,
            moex_override=args.moex_override,
            lang=(args.lang or "ru").strip() or "ru",
            timeout=max(5.0, float(args.timeout)),
            refresh=bool(args.refresh),
            readonly=bool(getattr(args, "readonly", False)),
        )
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    sys.stdout.write(text)
    if w and not getattr(args, "readonly", False):
        sys.stdout.write("\nПредупреждения:\n")
        for x in w:
            sys.stdout.write(f"  • {x}\n")
    return 0
