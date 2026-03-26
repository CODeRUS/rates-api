#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Текстовые отчёты «наличные»: курс продажи (``cash``) и цепочка ➔ THB (``cash-thb``).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sources.cash_aggregate import unified_top_sell_offers

# (подпись в отчёте, ключ Banki из BANKI_REGIONS, city_id РBC или None).
# РБК только Москва/СПб; остальные — Banki.ru.
_CASH_LOCATIONS: Tuple[Tuple[str, str, Optional[int]], ...] = (
    ("Москва", "moskva", 1),
    ("Санкт-Петербург", "sankt-peterburg", 2),
    ("Казань", "kazan", None),
    ("Ростов-на-Дону", "rostov-na-donu", None),
    ("Новосибирск", "novosibirsk", None),
    ("Красноярск", "krasnoyarsk", None),
)

_FIAT: Tuple[Tuple[str, int], ...] = (
    ("USD", 3),
    ("EUR", 2),
    ("CNY", 423),
)


def _locations(use_banki: bool) -> Tuple[Tuple[str, str, Optional[int]], ...]:
    if use_banki:
        return _CASH_LOCATIONS
    return tuple(loc for loc in _CASH_LOCATIONS if loc[2] is not None)


def _tt_thb_branch() -> Tuple[Optional[dict], str]:
    """THB за 1 USD/EUR/CNY и подпись филиала TT (как в rbc_ttexchange)."""
    from sources.rbc_ttexchange import _ttex_thb_per_fiat

    thb_map, _notes, branch = _ttex_thb_per_fiat()
    return thb_map, branch


def build_cash_report_text(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
) -> Tuple[str, List[str]]:
    """
    Только курсы продажи наличной валюты (РБК + Banki).
    Порядок: валюты USD→EUR→CNY, города как в ``_CASH_LOCATIONS``.
    """
    warnings: List[str] = []
    lines: List[str] = [
        "Наличные: РБК + Banki.ru (топ по курсу продажи)",
        "",
    ]

    locs = _locations(use_banki)

    for fiat_code, cur_id in _FIAT:
        for city_label, banki_key, rbc_id in locs:
            lines.append(f"{fiat_code} {city_label}")
            offers, w = unified_top_sell_offers(
                fiat_code=fiat_code,
                banki_region_key=banki_key,
                rbc_city_id=rbc_id,
                rbc_currency_id=cur_id,
                top_n=top_n,
                timeout=timeout,
                use_banki=use_banki,
            )
            warnings.extend(w)
            if not offers:
                lines.append("(нет котировок sell)")
                lines.append("")
                warnings.append(f"Нет sell: {fiat_code} {city_label}")
                continue
            for o in offers:
                lines.append(f"{o.sell:.2f} | {o.bank_display} ({o.sources_label()})")
            lines.append("")

    full = "\n".join(lines).rstrip() + "\n"
    return full, warnings


def build_cash_thb_report_text(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
) -> Tuple[str, List[str]]:
    """
    Цепочка: продажа валюты у банка (RUB/ед.) × TT → RUB за 1 THB.
    В каждой строке: курс продажи в источнике | подразумеваемый RUB/THB | банк | TT.
    """
    warnings: List[str] = []
    thb_map, tt_branch = _tt_thb_branch()
    if not thb_map:
        warnings.append(
            "Нет курсов USD/EUR/CNY у TT Exchange — цепочки ➔ THB не посчитать."
        )
        thb_map = {}

    tt_label = f"TT {tt_branch}" if (tt_branch or "").strip() else "TT Exchange"

    lines: List[str] = [
        "Наличные ➔ THB: продажа (RUB/ед.) | RUB/THB | банк (источник) | обменник",
        "",
    ]

    locs = _locations(use_banki)

    for fiat_code, cur_id in _FIAT:
        thb_per = thb_map.get(fiat_code)
        for city_label, banki_key, rbc_id in locs:
            lines.append(f"{fiat_code} {city_label}")
            offers, w = unified_top_sell_offers(
                fiat_code=fiat_code,
                banki_region_key=banki_key,
                rbc_city_id=rbc_id,
                rbc_currency_id=cur_id,
                top_n=top_n,
                timeout=timeout,
                use_banki=use_banki,
            )
            warnings.extend(w)
            if not offers:
                lines.append("(нет котировок sell)")
                lines.append("")
                warnings.append(f"Нет sell: {fiat_code} {city_label}")
                continue
            if thb_per is not None and thb_per > 0:
                for o in offers:
                    implied = o.sell / thb_per
                    lines.append(
                        f"{o.sell:.2f} | {implied:.2f} | {o.bank_display} "
                        f"({o.sources_label()}) | {tt_label}"
                    )
                lines.append("")
            else:
                for o in offers:
                    lines.append(
                        f"{o.sell:.2f} | — | {o.bank_display} "
                        f"({o.sources_label()}) | (нет THB/{fiat_code} у TT)"
                    )
                lines.append("")
                warnings.append(f"Нет TT {fiat_code}: {city_label}")

    full = "\n".join(lines).rstrip() + "\n"
    return full, warnings


def format_cash_report_with_warnings(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
) -> str:
    body, w = build_cash_report_text(
        top_n=top_n, timeout=timeout, use_banki=use_banki
    )
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def format_cash_thb_report_with_warnings(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
) -> str:
    body, w = build_cash_thb_report_text(
        top_n=top_n, timeout=timeout, use_banki=use_banki
    )
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def cash_subcommand_help() -> str:
    return (
        "cash — курсы продажи наличной валюты: РБК (Москва, СПб) + Banki.ru "
        "(ещё Казань, Ростов-на-Дону, Новосибирск, Красноярск).\n"
        "  cash [--top N] [--no-banki] [--refresh]   N по умолчанию 3; "
        "--no-banki только РБК (два города); --refresh зарезервирован.\n"
        "Цепочку с TT Exchange см. команду cash-thb."
    )


def cash_thb_subcommand_help() -> str:
    return (
        "cash-thb — те же топы по продажи × курс TT Exchange → RUB за 1 THB.\n"
        "Формат строки: продажа (RUB/ед.) | RUB/THB | банк (источник) | TT.\n"
        "  cash-thb [--top N] [--no-banki] [--refresh]   как у cash."
    )


def _parse_cash_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--top", type=int, default=3, help="Число строк по городу")
    p.add_argument(
        "--no-banki",
        action="store_true",
        help="Не запрашивать Banki.ru (только РБК, Москва и СПб)",
    )
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
    text = format_cash_report_with_warnings(
        top_n=args.top,
        use_banki=not args.no_banki,
    )
    sys.stdout.write(text)
    return 0


def main_cash_thb_cli(argv: List[str]) -> int:
    args = _parse_cash_argv(argv)
    if args.help:
        print(cash_thb_subcommand_help())
        return 0
    if args.top < 1:
        print("--top должен быть >= 1", file=sys.stderr)
        return 2
    text = format_cash_thb_report_with_warnings(
        top_n=args.top,
        use_banki=not args.no_banki,
    )
    sys.stdout.write(text)
    return 0
