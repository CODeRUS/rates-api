#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Топ филиалов TT Exchange по наличному приёму USD/EUR/CNY → THB + строка Ex24 (тот же формат).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
import urllib.error
from typing import Any, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ttexchange_fiat_rates import (
    fiat_buy_thb_per_unit,
    normalize_ttexchange_branch_label,
)

_TT_API_MOD: Any = None


def _ttexchange_api_module() -> Any:
    """Загрузка ``ttexchange_api`` без импорта ``sources.ttexchange`` (циклы с rates_sources)."""
    global _TT_API_MOD
    if _TT_API_MOD is None:
        path = _ROOT / "sources" / "ttexchange" / "ttexchange_api.py"
        spec = importlib.util.spec_from_file_location(
            "rates_ttexchange_api_iso",
            str(path),
        )
        if spec is None or spec.loader is None:
            raise ImportError("ttexchange_api")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _TT_API_MOD = mod
    return _TT_API_MOD


def _branch_skipped_as_closed(raw_name: str) -> bool:
    return "closed" in raw_name.lower()


def _ex24_rub_thb_module() -> Any:
    from sources.ex24 import ex24_rub_thb as mod

    return mod


def _format_table_row(name: str, u: Optional[float], e: Optional[float], c: Optional[float]) -> str:
    def cell(x: Optional[float]) -> str:
        return f"{x:.2f}" if x is not None else "—"

    return f"{cell(u):>7}  {cell(e):>7}  {cell(c):>7}  {name}"


def build_exchange_report_text(
    *,
    top_n: int = 10,
    lang: str = "ru",
    timeout: float = 28.0,
) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    ttx = _ttexchange_api_module()
    stores = ttx.get_stores(lang, timeout=timeout)
    if not isinstance(stores, list):
        return "Нет списка филиалов TT Exchange.\n", ["TT /stores не список"]

    scored: List[Tuple[str, float, Optional[float], Optional[float], str]] = []
    for row in stores:
        if not isinstance(row, dict):
            continue
        bid = row.get("branch_id")
        if bid is None:
            continue
        bid_s = str(bid)
        raw_name = str(row.get("name") or "").strip()
        if _branch_skipped_as_closed(raw_name):
            continue
        label = normalize_ttexchange_branch_label(raw_name or bid_s)
        try:
            cur = ttx.get_currencies(bid_s, is_main=False, timeout=timeout)
        except (
            OSError,
            ValueError,
            urllib.error.HTTPError,
            urllib.error.URLError,
            json.JSONDecodeError,
            TimeoutError,
        ) as exc:
            warnings.append(f"TT курсы {label} ({bid_s}): {exc}")
            continue
        usd = fiat_buy_thb_per_unit(cur, "USD")
        if usd is None:
            continue
        eur = fiat_buy_thb_per_unit(cur, "EUR")
        cny = fiat_buy_thb_per_unit(cur, "CNY")
        scored.append((label, usd, eur, cny, bid_s))

    scored.sort(
        key=lambda t: (
            -t[1],
            -(t[2] if t[2] is not None else -1e9),
            -(t[3] if t[3] is not None else -1e9),
        )
    )
    top = scored[: max(0, top_n)]

    lines: List[str] = [
        "Обмен наличные → THB (TT Exchange и Ex24), THB за 1 ед. валюты",
        "",
        f"{'USD':>7}  {'EUR':>7}  {'CNY':>7}  Филиал",
    ]

    if not top:
        lines.append("(нет филиалов с курсом USD)")
        warnings.append("Ни у одного филиала не найден USD (buy) в курсах TT")
    else:
        for label, u, eur, cny, _bid in top:
            lines.append(_format_table_row(label, u, eur, cny))

    lines.append("")

    e24 = _ex24_rub_thb_module()
    html = e24.load_ex24_main_html(timeout=timeout)
    ex_u = ex_e = ex_c = None
    if html:
        ex_u = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "USD")
        ex_e = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "EUR")
        ex_c = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "CNY")
    else:
        warnings.append("Ex24: не удалось загрузить главную страницу")

    lines.append(_format_table_row("Ex24", ex_u, ex_e, ex_c))
    full = "\n".join(lines).rstrip() + "\n"
    return full, warnings


def format_exchange_report_with_warnings(
    *, top_n: int = 10, lang: str = "ru", timeout: float = 28.0
) -> str:
    body, w = build_exchange_report_text(top_n=top_n, lang=lang, timeout=timeout)
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def exchange_subcommand_help() -> str:
    return (
        "exchange — топ филиалов TT Exchange по приёму наличных USD/EUR/CNY→THB "
        "(THB за 1 ед.; сортировка по USD), затем строка Ex24 в том же формате.\n"
        "  exchange [--top N] [--lang ru|en] [--refresh]   N по умолчанию 10; --refresh зарезервирован."
    )


def _parse_exchange_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--top", type=int, default=10, help="Число филиалов")
    p.add_argument("--lang", type=str, default="ru", help="Язык подписей TT store API")
    p.add_argument("--timeout", type=float, default=28.0, help="Таймаут HTTP на запрос")
    p.add_argument("--refresh", action="store_true", help="Зарезервировано")
    p.add_argument("-h", "--help", action="store_true")
    return p.parse_args(argv)


def main_exchange_cli(argv: List[str]) -> int:
    args = _parse_exchange_argv(argv)
    if args.help:
        print(exchange_subcommand_help())
        return 0
    if args.top < 1:
        print("--top должен быть >= 1", file=sys.stderr)
        return 2
    text = format_exchange_report_with_warnings(
        top_n=args.top,
        lang=(args.lang or "ru").strip() or "ru",
        timeout=max(5.0, float(args.timeout)),
    )
    sys.stdout.write(text)
    return 0
