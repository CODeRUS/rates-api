#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Текстовый отчёт «наличные РБК»: топ банков по продаже (Москва/СПб) и пары ➔ THB через TT Exchange.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sources.rbc_cash_json import fetch_cash_rates_json, top_sell_offers

_CITIES: Tuple[Tuple[int, str], ...] = (
    (1, "Москва"),
    (2, "Санкт-Петербург"),
)

_FIAT: Tuple[Tuple[str, int], ...] = (
    ("USD", 3),
    ("EUR", 2),
    ("CNY", 423),
)


def _tt_thb_branch() -> Tuple[Optional[Dict[str, float]], str]:
    """THB за 1 USD/EUR/CNY и подпись филиала TT (как в rbc_ttexchange)."""
    from sources.rbc_ttexchange import _ttex_thb_per_fiat

    thb_map, _notes, branch = _ttex_thb_per_fiat()
    return thb_map, branch


def build_cash_report_text(*, top_n: int = 3, timeout: float = 22.0) -> Tuple[str, List[str]]:
    """
    Возвращает (текст отчёта, предупреждения).
    Порядок: валюты USD→EUR→CNY, города Москва→СПб, внутри города по возрастанию sell.
    """
    warnings: List[str] = []
    thb_map, tt_branch = _tt_thb_branch()
    if not thb_map:
        warnings.append("Нет курсов USD/EUR/CNY у TT Exchange — пары ➔ THB не посчитать.")
        thb_map = {}

    tt_label = f"TT {tt_branch}" if (tt_branch or "").strip() else "TT Exchange"

    lines: List[str] = [
        "Наличные РБК (топ по курсу продажи)",
        "",
    ]

    pair_lines: List[str] = [
        "",
        "Пары ➔ THB (TT Exchange)",
        "",
    ]

    for fiat_code, cur_id in _FIAT:
        thb_per = thb_map.get(fiat_code)
        for city_id, city_label in _CITIES:
            lines.append(f"{fiat_code} {city_label}")
            data = fetch_cash_rates_json(
                city=city_id, currency_id=cur_id, timeout=timeout
            )
            if not isinstance(data, dict):
                lines.append("(нет данных РБК)")
                lines.append("")
                warnings.append(f"РБК JSON: {fiat_code} {city_label}")
                continue
            banks = data.get("banks")
            offers = top_sell_offers(banks, n=top_n)
            if not offers:
                lines.append("(нет котировок sell)")
                lines.append("")
                warnings.append(f"Нет sell: {fiat_code} {city_label}")
                continue
            for sell, _raw, short in offers:
                lines.append(f"{sell:.2f} | {short} (РБК)")
            lines.append("")

            if thb_per is not None and thb_per > 0:
                pair_lines.append(f"{fiat_code} {city_label} ➔ THB")
                for sell, _raw, short in offers:
                    implied = sell / thb_per
                    pair_lines.append(
                        f"{implied:.2f} | {short} (РБК) | {tt_label}"
                    )
                pair_lines.append("")
            else:
                pair_lines.append(f"{fiat_code} {city_label} ➔ THB")
                pair_lines.append(f"(нет THB/{fiat_code} у TT)")
                pair_lines.append("")

    full = "\n".join(lines + pair_lines).rstrip() + "\n"
    return full, warnings


def format_cash_report_with_warnings(
    *, top_n: int = 3, timeout: float = 22.0
) -> str:
    body, w = build_cash_report_text(top_n=top_n, timeout=timeout)
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def cash_subcommand_help() -> str:
    return (
        "cash — курсы продажи валюты (РБК): топ отделений по Москве и СПб, затем цепочки ➔ THB через TT Exchange.\n"
        "  cash [--top N] [--refresh]   N по умолчанию 3; --refresh зарезервирован для будущего кеша."
    )


def _parse_cash_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--top", type=int, default=3, help="Число строк по городу (по умолчанию 3)")
    p.add_argument("--refresh", action="store_true", help="Зарезервировано")
    p.add_argument("-h", "--help", action="store_true")
    return p.parse_args(argv)


def main_cash_cli(argv: List[str]) -> int:
    args = _parse_cash_argv(argv)
    if args.help:
        print(cash_subcommand_help())
        return 0
    if args.top < 1:
        print("--top должен быть >= 1", file=sys.stderr)
        return 2
    text = format_cash_report_with_warnings(top_n=args.top)
    sys.stdout.write(text)
    return 0
