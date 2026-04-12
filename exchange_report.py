#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Топ филиалов TT Exchange по наличному приёму USD/EUR/CNY → THB.
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
from cash_report import normalize_cash_fiat
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

# Сколько строк филиалов кладём в один L2-снимок (top_n при чтении только обрезает текст).
_MAX_EXCHANGE_L2_BRANCH_ROWS = 200


def _ex_l2_key(*, lang: str, timeout: float) -> str:
    """Ключ L2 без top_n и без fiat: один мультивалютный снимок на (lang, timeout); top и --fiat — при чтении."""
    ident: Dict[str, Any] = {
        "lang": str(lang),
        "timeout": round(float(timeout), 3),
    }
    return f"l2:exchange:{ucc.stable_digest(ident)}"


def _slice_exchange_cached_text(body: str, top_n: int) -> str:
    """Обрезать сохранённый текст отчёта до первых top_n строк таблицы (после 3 строк заголовка)."""
    if top_n <= 0:
        return body
    s = body.rstrip("\n")
    if not s:
        return body
    lines = s.split("\n")
    if len(lines) <= 3:
        return body
    head, data = lines[:3], lines[3:]
    if not data:
        return body
    out = head + data[:top_n]
    return "\n".join(out) + "\n"


def _exchange_cached_body_is_multicurrency(lines: List[str]) -> bool:
    if len(lines) < 3:
        return False
    h = lines[2]
    return "USD" in h and "EUR" in h and "CNY" in h and "Филиал" in h


def _parse_exchange_multicurrency_row(line: str) -> Optional[Tuple[Optional[float], Optional[float], Optional[float], str]]:
    """Разбор строки ``_format_table_row`` (7+2+7+2+7+2+name)."""
    if len(line) < 27:
        return None

    def cell(s: str) -> Optional[float]:
        s = s.strip()
        if not s or s == "—":
            return None
        try:
            return float(s)
        except ValueError:
            return None

    u = cell(line[0:7])
    e = cell(line[9:16])
    c = cell(line[18:25])
    name = line[27:].strip()
    if not name:
        return None
    return (u, e, c, name)


def _exchange_multicurrency_body_to_fiat(body: str, fiat_code: str, top_n: int) -> str:
    """Из кешированного мультиколоночного текста — одноколоночный отчёт по USD/EUR/CNY."""
    col_i = {"USD": 0, "EUR": 1, "CNY": 2}.get(fiat_code)
    if col_i is None:
        return _slice_exchange_cached_text(body, top_n)
    lines = body.rstrip("\n").split("\n")
    if len(lines) <= 3:
        return body
    head3, data = lines[:3], lines[3:]
    if not _exchange_cached_body_is_multicurrency(head3):
        return _slice_exchange_cached_text(body, top_n)
    rows: List[Tuple[float, str]] = []
    for ln in data:
        p = _parse_exchange_multicurrency_row(ln)
        if p is None:
            continue
        vals, name = p[:3], p[3]
        v = vals[col_i]
        if v is not None:
            rows.append((v, name))
    rows.sort(key=lambda t: -t[0])
    cap = rows[: max(0, top_n)]
    hdr = [
        f"Обмен наличные → THB (TT Exchange), только {fiat_code}, THB за 1 {fiat_code}",
        "",
        f"{fiat_code:>7}  Филиал",
    ]
    if not cap:
        out = hdr + [f"(нет филиалов с курсом {fiat_code} в кешированной таблице)"]
    else:
        out = hdr + [f"{rate:>7.2f}  {label}" for rate, label in cap]
    return "\n".join(out).rstrip() + "\n"


def _exchange_apply_top_n_to_cached_body(
    body: str, fiat_norm: Optional[str], top_n: int
) -> str:
    if fiat_norm:
        return _exchange_multicurrency_body_to_fiat(body, fiat_norm, top_n)
    return _slice_exchange_cached_text(body, top_n)


def _ex_deps_for_keys(doc: Dict[str, Any], keys: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    l1 = doc.get("l1") or {}
    for k in keys:
        ent = l1.get(k)
        if isinstance(ent, dict) and int(ent.get("version", 0)) > 0:
            out[k] = int(ent["version"])
    return out


def best_fiat_buy_thb_across_branches(
    *,
    fiat_code: str,
    lang: str = "ru",
    timeout: float = 28.0,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    readonly: bool = False,
) -> Tuple[Optional[float], List[str]]:
    """
    Максимальный курс TT (THB за 1 ед. ``fiat_code``) среди открытых филиалов.

    В отличие от ``build_exchange_report_text``, не отбрасывает филиалы без USD —
    подходит для EUR/CNY. Обновляет L1 unified-кеш курсов так же, как отчёт exchange.
    """
    code = (fiat_code or "").strip().upper()
    warnings: List[str] = []
    if code not in ("USD", "EUR", "CNY"):
        warnings.append(f"Неподдерживаемая валюта TT: {fiat_code!r}")
        return None, warnings

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    lang = (lang or "ru").strip() or "ru"
    ttx = _ttexchange_api_module()
    stores_key = f"ex:l1:stores:{lang}"
    stores_list: Any = None
    if not refresh:
        hit_s = ucc.l1_get_valid(doc, stores_key)
        if hit_s is not None:
            stores_list = hit_s[1]
    if readonly and stores_list is None:
        t_any = ucc.l1_get_any(doc, stores_key)
        if t_any is not None:
            stores_list = t_any[1]
    if refresh or stores_list is None:
        if readonly:
            if not isinstance(stores_list, list):
                warnings.append("readonly: нет списка филиалов TT в L1")
                return None, warnings
        else:
            stores_list = ttx.get_stores(lang, timeout=timeout)
            if isinstance(stores_list, list):
                ucc.l1_set(
                    doc,
                    stores_key,
                    stores_list,
                    ttl_sec=ucc.TTL_L1_EXCHANGE_STORES_SEC,
                )

    if not isinstance(stores_list, list):
        warnings.append("TT /stores не список")
        return None, warnings

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

    def _fetch_currencies(job: Tuple[str, str]) -> Any:
        _lb, bid_s = job
        cur_key = f"ex:l1:cur:{bid_s}:{lang}"
        if not refresh:
            hit = ucc.l1_get_valid(doc, cur_key)
            if hit is not None:
                cur_cached = hit[1]
                if fiat_buy_thb_per_unit(cur_cached, code) is not None:
                    return cur_cached
        if readonly:
            t_any = ucc.l1_get_any(doc, cur_key)
            if t_any is not None:
                return t_any[1]
            return None
        cur = ttx.get_currencies(bid_s, is_main=False, timeout=timeout)
        ucc.l1_set(
            doc,
            cur_key,
            cur,
            ttl_sec=ucc.TTL_L1_EXCHANGE_CUR_SEC,
        )
        return cur

    best: Optional[float] = None
    for (_label, _bid_s), cur, exc in map_bounded(
        jobs,
        _fetch_currencies,
        max_workers=parallel_max_workers,
    ):
        if exc is not None:
            if isinstance(exc, _TT_FETCH_ERRORS):
                continue
            raise exc
        if cur is None:
            continue
        v = fiat_buy_thb_per_unit(cur, code)
        if v is None:
            continue
        if best is None or v > best:
            best = v

    if best is None:
        warnings.append(f"Ни у одного филиала не найден курс TT для {code}")

    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    return best, warnings


def build_exchange_report_text(
    *,
    top_n: int = 10,
    lang: str = "ru",
    timeout: float = 28.0,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    readonly: bool = False,
    fiat: Optional[str] = None,
) -> Tuple[str, List[str]]:
    global _unified_served_stale_l2

    fiat_norm: Optional[str] = normalize_cash_fiat(fiat) if fiat else None

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    lang = (lang or "ru").strip() or "ru"
    l2_key = _ex_l2_key(lang=lang, timeout=timeout)
    from_stale_l2 = False

    allow_stale_ex = bool(unified_allow_stale or readonly)
    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_EXCHANGE_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and allow_stale_ex:
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
            body = str(ent.get("text") or "")
            if body.strip():
                body = _exchange_apply_top_n_to_cached_body(body, fiat_norm, top_n)
                w = list((ent.get("payload") or {}).get("warnings") or [])
                dep_ok = (not deps) or ucc.l2_deps_match(doc, deps)
                if readonly:
                    if not dep_ok:
                        w.append(
                            "readonly: L2 exchange — зависимости L1 не совпадают; показан снимок L2."
                        )
                    _unified_served_stale_l2 = from_stale_l2
                    return body, w
                if dep_ok:
                    _unified_served_stale_l2 = from_stale_l2
                    return body, w

    _unified_served_stale_l2 = False
    if readonly:
        return (
            "Обмен наличные → THB (readonly)\n\nНет L2 exchange в unified-кеше для этих параметров.\n",
            ["--readonly: нет кешированного отчёта exchange."],
        )
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
    gate_fiat = fiat_norm or "USD"

    def _fetch_currencies(job: Tuple[str, str]) -> Any:
        _lb, bid_s = job
        cur_key = f"ex:l1:cur:{bid_s}:{lang}"
        if not refresh:
            hit = ucc.l1_get_valid(doc, cur_key)
            if hit is not None:
                cur_cached = hit[1]
                if fiat_buy_thb_per_unit(cur_cached, gate_fiat) is not None:
                    return cur_cached
        cur = ttx.get_currencies(bid_s, is_main=False, timeout=timeout)
        ucc.l1_set(
            doc,
            cur_key,
            cur,
            ttl_sec=ucc.TTL_L1_EXCHANGE_CUR_SEC,
        )
        return cur

    scored: List[Tuple[str, float, Optional[float], Optional[float], str]] = []
    scored_one: List[Tuple[str, float, str]] = []
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
        if cur is None:
            continue
        if fiat_norm:
            rate = fiat_buy_thb_per_unit(cur, fiat_norm)
            if rate is not None:
                scored_one.append((label, rate, bid_s))
        usd = fiat_buy_thb_per_unit(cur, "USD")
        if usd is not None:
            eur = fiat_buy_thb_per_unit(cur, "EUR")
            cny = fiat_buy_thb_per_unit(cur, "CNY")
            scored.append((label, usd, eur, cny, bid_s))
        elif not fiat_norm:
            continue

    def _lines_multi(
        rows: List[Tuple[str, float, Optional[float], Optional[float], str]],
    ) -> List[str]:
        hdr = [
            "Обмен наличные → THB (TT Exchange), THB за 1 ед. валюты",
            "",
            f"{'USD':>7}  {'EUR':>7}  {'CNY':>7}  Филиал",
        ]
        if not rows:
            return hdr + ["(нет филиалов с курсом USD)"]
        return hdr + [
            _format_table_row(label, u, eur, cny)
            for label, u, eur, cny, _bid in rows
        ]

    scored.sort(
        key=lambda t: (
            -t[1],
            -(t[2] if t[2] is not None else -1e9),
            -(t[3] if t[3] is not None else -1e9),
        )
    )
    cap_mc = min(len(scored), _MAX_EXCHANGE_L2_BRANCH_ROWS)
    rows_store_mc = scored[: max(0, cap_mc)]
    full_store = "\n".join(_lines_multi(rows_store_mc)).rstrip() + "\n"
    if not scored:
        warnings.append("Ни у одного филиала не найден USD (buy) в курсах TT")

    if fiat_norm:
        scored_one.sort(key=lambda t: -t[1])
        rows_show_one = scored_one[: max(0, top_n)]

        def _lines_fiat(rows: List[Tuple[str, float, str]]) -> List[str]:
            hdr = [
                f"Обмен наличные → THB (TT Exchange), только {fiat_norm}, THB за 1 {fiat_norm}",
                "",
                f"{fiat_norm:>7}  Филиал",
            ]
            if not rows:
                return hdr + [f"(нет филиалов с курсом {fiat_norm})"]
            return hdr + [f"{rate:>7.2f}  {label}" for label, rate, _bid in rows]

        if not scored_one:
            warnings.append(
                f"Ни у одного филиала не найден {fiat_norm} (buy) в курсах TT"
            )
        lines_show = _lines_fiat(rows_show_one)
        full = "\n".join(lines_show).rstrip() + "\n"
    else:
        rows_show_mc = scored[: max(0, top_n)]
        lines_show = _lines_multi(rows_show_mc)
        full = "\n".join(lines_show).rstrip() + "\n"

    deps_map = _ex_deps_for_keys(doc, dep_key_list)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_EXCHANGE_SEC,
        text=full_store,
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
    readonly: bool = False,
    fiat: Optional[str] = None,
) -> str:
    body, w = build_exchange_report_text(
        top_n=top_n,
        lang=lang,
        timeout=timeout,
        parallel_max_workers=parallel_max_workers,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
        readonly=readonly,
        fiat=fiat,
    )
    if readonly:
        return body
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def exchange_subcommand_help() -> str:
    return (
        "exchange — топ филиалов TT Exchange по приёму наличных USD/EUR/CNY→THB "
        "(THB за 1 ед.; по умолчанию сортировка по USD).\n"
        "  exchange [--top N] [--lang ru|en] [--timeout СЕК] [--fiat USD|EUR|CNY] [--refresh]   "
        "N по умолчанию 10; --fiat — одна колонка и сортировка по этой валюте (филиалы без неё пропускаются).\n"
        "  --refresh — заново запросить API и обновить unified-кеш.\n"
        "  Параллельные запросы курсов TT: переменная RATES_PARALLEL_MAX_WORKERS (по умолчанию 12)."
    )


def _parse_exchange_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--top", type=int, default=10, help="Число филиалов")
    p.add_argument("--lang", type=str, default="ru", help="Язык подписей TT store API")
    p.add_argument("--timeout", type=float, default=28.0, help="Таймаут HTTP на запрос")
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Пропустить чтение L1/L2 unified, заново запросить TT API",
    )
    p.add_argument(
        "--readonly",
        action="store_true",
        help="Только кеш (в т.ч. с истёкшим TTL), без сети",
    )
    p.add_argument(
        "--fiat",
        type=str,
        default=None,
        metavar="USD|EUR|CNY",
        help="Только выбранная валюта в таблице и в сортировке",
    )
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
    fiat_kw: Optional[str] = None
    if getattr(args, "fiat", None):
        try:
            fiat_kw = normalize_cash_fiat(args.fiat)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
    text = format_exchange_report_with_warnings(
        top_n=args.top,
        lang=(args.lang or "ru").strip() or "ru",
        timeout=max(5.0, float(args.timeout)),
        refresh=bool(args.refresh),
        readonly=bool(getattr(args, "readonly", False)),
        fiat=fiat_kw,
    )
    sys.stdout.write(text)
    return 0
