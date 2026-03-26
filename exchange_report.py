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
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rates_parallel import map_bounded
import rates_unified_cache as ucc
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


_unified_served_stale_l2: bool = False

_TT_FETCH_ERRORS = (
    OSError,
    ValueError,
    urllib.error.HTTPError,
    urllib.error.URLError,
    json.JSONDecodeError,
    TimeoutError,
)


def _ex_l2_key(*, top_n: int, lang: str, timeout: float) -> str:
    ident: Dict[str, Any] = {
        "top_n": int(top_n),
        "lang": str(lang),
        "timeout": round(float(timeout), 3),
    }
    return f"l2:exchange:{ucc.stable_digest(ident)}"


def _ex_deps_for_keys(doc: Dict[str, Any], keys: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    l1 = doc.get("l1") or {}
    for k in keys:
        ent = l1.get(k)
        if isinstance(ent, dict) and int(ent.get("version", 0)) > 0:
            out[k] = int(ent["version"])
    return out


def build_exchange_report_text(
    *,
    top_n: int = 10,
    lang: str = "ru",
    timeout: float = 28.0,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
) -> Tuple[str, List[str]]:
    global _unified_served_stale_l2

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    l2_key = _ex_l2_key(top_n=top_n, lang=lang, timeout=timeout)
    lang = (lang or "ru").strip() or "ru"
    from_stale_l2 = False

    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_EXCHANGE_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and unified_allow_stale:
            ent = ucc.l2_get(
                doc,
                l2_key,
                ttl_sec=ucc.TTL_L2_EXCHANGE_SEC,
                require_fresh=False,
                allow_stale=True,
            )
            if ent is not None:
                from_stale_l2 = True
        if ent is not None:
            deps = ent.get("deps") or {}
            if (not deps) or ucc.l2_deps_match(doc, deps):
                body = str(ent.get("text") or "")
                if body.strip():
                    w = list((ent.get("payload") or {}).get("warnings") or [])
                    _unified_served_stale_l2 = from_stale_l2
                    return body, w

    _unified_served_stale_l2 = False
    warnings: List[str] = []
    ttx = _ttexchange_api_module()
    stores_key = f"ex:l1:stores:{lang}"
    stores_list: Any = None
    if not refresh:
        hit_s = ucc.l1_get_valid(doc, stores_key)
        if hit_s is not None:
            stores_list = hit_s[1]
    if refresh or stores_list is None:
        stores_list = ttx.get_stores(lang, timeout=timeout)
        if isinstance(stores_list, list):
            ucc.l1_set(
                doc,
                stores_key,
                stores_list,
                ttl_sec=ucc.TTL_L1_EXCHANGE_STORES_SEC,
            )

    if not isinstance(stores_list, list):
        return "Нет списка филиалов TT Exchange.\n", ["TT /stores не список"]

    jobs: List[Tuple[str, str]] = []
    for row in stores_list:
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
        jobs.append((label, bid_s))

    dep_key_list: List[str] = [stores_key]

    def _fetch_currencies(job: Tuple[str, str]) -> Any:
        _lb, bid_s = job
        cur_key = f"ex:l1:cur:{bid_s}:{lang}"
        if not refresh:
            hit = ucc.l1_get_valid(doc, cur_key)
            if hit is not None:
                return hit[1]
        cur = ttx.get_currencies(bid_s, is_main=False, timeout=timeout)
        ucc.l1_set(
            doc,
            cur_key,
            cur,
            ttl_sec=ucc.TTL_L1_EXCHANGE_CUR_SEC,
        )
        return cur

    scored: List[Tuple[str, float, Optional[float], Optional[float], str]] = []
    for (label, bid_s), cur, exc in map_bounded(
        jobs,
        _fetch_currencies,
        max_workers=parallel_max_workers,
    ):
        dep_key_list.append(f"ex:l1:cur:{bid_s}:{lang}")
        if exc is not None:
            if isinstance(exc, _TT_FETCH_ERRORS):
                warnings.append(f"TT курсы {label} ({bid_s}): {exc}")
                continue
            raise exc
        assert cur is not None
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

    e24_key = "ex:l1:e24"
    ex_u = ex_e = ex_c = None
    e24_from_l1 = False
    if not refresh:
        hit_e = ucc.l1_get_valid(doc, e24_key)
        if hit_e is not None:
            pl = hit_e[1]
            if isinstance(pl, dict):
                ex_u = pl.get("usd")
                ex_e = pl.get("eur")
                ex_c = pl.get("cny")
                e24_from_l1 = True
    if refresh or not e24_from_l1:
        e24 = _ex24_rub_thb_module()
        html = e24.load_ex24_main_html(timeout=timeout)
        if html:
            ex_u = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "USD")
            ex_e = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "EUR")
            ex_c = e24.parse_ex24_cash_fiat_thb_per_fiat_unit(html, "CNY")
        else:
            ex_u = ex_e = ex_c = None
            warnings.append("Ex24: не удалось загрузить главную страницу")
        ucc.l1_set(
            doc,
            e24_key,
            {"usd": ex_u, "eur": ex_e, "cny": ex_c},
            ttl_sec=ucc.TTL_L1_EX24_SEC,
        )
    dep_key_list.append(e24_key)

    lines.append(_format_table_row("Ex24", ex_u, ex_e, ex_c))
    full = "\n".join(lines).rstrip() + "\n"
    deps_map = _ex_deps_for_keys(doc, dep_key_list)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_EXCHANGE_SEC,
        text=full,
        deps=deps_map,
        payload={"warnings": warnings},
    )
    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    return full, warnings


def format_exchange_report_with_warnings(
    *,
    top_n: int = 10,
    lang: str = "ru",
    timeout: float = 28.0,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
) -> str:
    body, w = build_exchange_report_text(
        top_n=top_n,
        lang=lang,
        timeout=timeout,
        parallel_max_workers=parallel_max_workers,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
    )
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def exchange_subcommand_help() -> str:
    return (
        "exchange — топ филиалов TT Exchange по приёму наличных USD/EUR/CNY→THB "
        "(THB за 1 ед.; сортировка по USD), затем строка Ex24 в том же формате.\n"
        "  exchange [--top N] [--lang ru|en] [--refresh]   N по умолчанию 10; --refresh зарезервирован.\n"
        "  Параллельные запросы курсов TT: переменная RATES_PARALLEL_MAX_WORKERS (по умолчанию 12)."
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
