#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отчёт USDT: P2P RUB/USDT (Bybit, HTX) и котировки USDT/THB (Bitkub, Binance TH).

Кеш по умолчанию отдельный от сводки ``rates.py`` (переменная ``RATES_USDT_CACHE_FILE``).
TTL задаётся :data:`USDT_CACHE_TTL_SEC`. В боте админ может сбросить кеш USDT: ``/refresh usdt``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from rates_parallel import map_bounded
import rates_unified_cache as ucc

USDT_CACHE_VERSION = 1

# Выставляется в compute_usdt_report: True, если ответ взят из L2 с истёкшим TTL (для фонового обновления в боте).
_unified_served_stale_l2: bool = False
USDT_CACHE_TTL_SEC = 60

_USDT_CACHE_OVERRIDE = (os.environ.get("RATES_USDT_CACHE_FILE") or "").strip()
_USDT_CACHE_OVERRIDE_PATH = Path(_USDT_CACHE_OVERRIDE) if _USDT_CACHE_OVERRIDE else None
if _USDT_CACHE_OVERRIDE_PATH is not None and not _USDT_CACHE_OVERRIDE_PATH.is_absolute():
    _USDT_CACHE_OVERRIDE_PATH = (_SCRIPT_DIR / _USDT_CACHE_OVERRIDE_PATH).resolve()
USDT_CACHE_FILE = (
    _USDT_CACHE_OVERRIDE_PATH if _USDT_CACHE_OVERRIDE_PATH is not None else _SCRIPT_DIR / ".rates_usdt_cache.json"
)


def _usdt_cache_key() -> Dict[str, Any]:
    return {"v": USDT_CACHE_VERSION}


def _load_stale_usdt_cache(path: Path) -> Optional[Tuple[Dict[str, Any], float]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("v") != USDT_CACHE_VERSION:
        return None
    saved = float(raw.get("saved_unix", 0))
    return raw, saved


def _usdt_cache_valid(raw: Dict[str, Any], saved: float, key: Dict[str, Any]) -> bool:
    if time.time() - saved > USDT_CACHE_TTL_SEC:
        return False
    return raw.get("key") == key


_UsdtParallelBranch = Tuple[Dict[str, Optional[float]], Dict[str, Optional[float]], List[str]]

_USDT_BRANCH_KEYS: Tuple[str, ...] = ("bybit", "htx", "bitkub", "binance", "fly", "it_obmen")


def _usdt_fetch_bybit_branch() -> _UsdtParallelBranch:
    from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp

    rub: Dict[str, Optional[float]] = {
        "bybit_cash": None,
        "bybit_transfer": None,
    }
    w: List[str] = []
    try:
        ia, ib = bp.fetch_best_cash_and_bank_transfer_items(
            size=20,
            verification_filter=0,
            target_usdt=bp.DEFAULT_TARGET_USDT,
            min_completion=99.0,
        )
    except RuntimeError as e:
        w.append(f"Bybit P2P: {e}")
        return rub, {}, w
    if ia:
        rub["bybit_cash"] = float(ia["price"])
    else:
        w.append(
            "Bybit: нет объявлений Cash Deposit (18) с completion≥99 "
            "(100 USDT, minAmount≥100·price)"
        )
    if ib:
        rub["bybit_transfer"] = float(ib["price"])
    else:
        w.append(
            "Bybit: нет объявлений только перевод (14, без 18) с completion≥99 "
            "(100 USDT, minAmount≥100·price)"
        )
    return rub, {}, w


def _usdt_fetch_htx_branch() -> _UsdtParallelBranch:
    from sources.htx_bitkub import htx_p2p_usdt_rub as hx

    rub: Dict[str, Optional[float]] = {
        "htx_cash": None,
        "htx_no_cash": None,
    }
    w: List[str] = []
    try:
        ha, hb = hx.fetch_best_cash_and_non_cash_offers(max_pages=30)
    except RuntimeError as e:
        w.append(f"HTX OTC: {e}")
        return rub, {}, w
    if ha:
        rub["htx_cash"] = float(ha["price"])
    else:
        w.append(
            "HTX: нет объявлений с наличными под фильтры "
            "(100 USDT, minTradeLimit≥100·price)"
        )
    if hb:
        rub["htx_no_cash"] = float(hb["price"])
    else:
        w.append(
            "HTX: нет объявлений без наличных под фильтры "
            "(100 USDT, minTradeLimit≥100·price)"
        )
    return rub, {}, w


def _usdt_fetch_bitkub_branch() -> _UsdtParallelBranch:
    from sources.bybit_bitkub import bitkub_usdt_thb as bk

    thb: Dict[str, Optional[float]] = {"bitkub_highest_bid": None}
    w: List[str] = []
    try:
        tk = bk.fetch_ticker()
    except RuntimeError as e:
        w.append(f"Bitkub: {e}")
        return {}, thb, w
    b = float(tk.get("highestBid") or 0)
    if b > 0:
        thb["bitkub_highest_bid"] = b
    else:
        w.append("Bitkub: нет highestBid для USDT")
    return {}, thb, w


def _usdt_fetch_binance_branch() -> _UsdtParallelBranch:
    from sources.binance_th.usdt_thb_book import fetch_bid_thb_per_usdt

    thb: Dict[str, Optional[float]] = {"binance_bid": None}
    w: List[str] = []
    try:
        thb["binance_bid"] = fetch_bid_thb_per_usdt()
    except RuntimeError as e:
        w.append(f"Binance TH: {e}")
    return {}, thb, w


def _usdt_fetch_fly_branch() -> _UsdtParallelBranch:
    """
    USDT/THB от userbot-источника Fly Currency (chatcash:fly_currency).
    Берем запись currency=USDTTHB, category=usdt_thb.
    """
    thb: Dict[str, Optional[float]] = {"fly_bid": None}
    w: List[str] = []
    doc = ucc.load_unified()
    hit = ucc.l1_get_valid(doc, "chatcash:fly_currency")
    if hit is None:
        w.append("Fly Currency: нет свежего userbot-кеша (chatcash:fly_currency)")
        return {}, thb, w
    payload = hit[1]
    if not isinstance(payload, list):
        w.append("Fly Currency: некорректный payload в userbot-кеше")
        return {}, thb, w
    for row in payload:
        if not isinstance(row, dict):
            continue
        if str(row.get("currency") or "").strip().upper() != "USDTTHB":
            continue
        if str(row.get("category") or "").strip().lower() != "usdt_thb":
            continue
        try:
            v = float(row.get("rate") or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            thb["fly_bid"] = v
            break
    if not thb["fly_bid"]:
        w.append("Fly Currency: нет USDT→THB в userbot-кеше")
    return {}, thb, w


def _usdt_fetch_it_obmen_branch() -> _UsdtParallelBranch:
    """
    USDT/THB от userbot-источника IT Обмен (chatcash:it_obmen_pattaya).
    Берем запись currency=USDTTHB, category=usdt_thb (до 1000 USDT).
    """
    thb: Dict[str, Optional[float]] = {"it_obmen_bid": None}
    w: List[str] = []
    doc = ucc.load_unified()
    hit = ucc.l1_get_valid(doc, "chatcash:it_obmen_pattaya")
    if hit is None:
        w.append("IT Обмен: нет свежего userbot-кеша (chatcash:it_obmen_pattaya)")
        return {}, thb, w
    payload = hit[1]
    if not isinstance(payload, list):
        w.append("IT Обмен: некорректный payload в userbot-кеше")
        return {}, thb, w
    for row in payload:
        if not isinstance(row, dict):
            continue
        if str(row.get("currency") or "").strip().upper() != "USDTTHB":
            continue
        if str(row.get("category") or "").strip().lower() != "usdt_thb":
            continue
        try:
            v = float(row.get("rate") or 0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            thb["it_obmen_bid"] = v
            break
    if not thb["it_obmen_bid"]:
        w.append("IT Обмен: нет USDT→THB в userbot-кеше")
    return {}, thb, w


def _usdt_l1_pack(pack: _UsdtParallelBranch) -> Dict[str, Any]:
    rpart, tpart, wpart = pack
    return {"rub": rpart, "thb": tpart, "warnings": wpart}


def _usdt_l1_unpack(payload: Any) -> _UsdtParallelBranch:
    if not isinstance(payload, dict):
        return {}, {}, []
    return (
        dict(payload.get("rub") or {}),
        dict(payload.get("thb") or {}),
        list(payload.get("warnings") or []),
    )


def _usdt_parallel_worker(branch: str) -> _UsdtParallelBranch:
    if branch == "bybit":
        return _usdt_fetch_bybit_branch()
    if branch == "htx":
        return _usdt_fetch_htx_branch()
    if branch == "bitkub":
        return _usdt_fetch_bitkub_branch()
    if branch == "binance":
        return _usdt_fetch_binance_branch()
    if branch == "fly":
        return _usdt_fetch_fly_branch()
    if branch == "it_obmen":
        return _usdt_fetch_it_obmen_branch()
    raise ValueError(branch)


def fetch_usdt_payload(
    *,
    parallel_max_workers: Optional[int] = None,
    unified_doc: Optional[Dict[str, Any]] = None,
    refresh: bool = True,
) -> Tuple[Dict[str, Any], List[str], Dict[str, int]]:
    """Собрать данные с API. При ``unified_doc`` и ``refresh=False`` — читать L1-ветки."""
    warnings: List[str] = []

    rub: Dict[str, Optional[float]] = {
        "bybit_cash": None,
        "bybit_transfer": None,
        "htx_cash": None,
        "htx_no_cash": None,
    }
    thb: Dict[str, Optional[float]] = {
        "bitkub_highest_bid": None,
        "binance_bid": None,
        "fly_bid": None,
        "it_obmen_bid": None,
    }

    def _work(branch: str) -> _UsdtParallelBranch:
        l1_key = f"usdt:l1:{branch}"
        if unified_doc is not None and not refresh:
            hit = ucc.l1_get_valid(unified_doc, key=l1_key)
            if hit is not None:
                _ver, payload = hit
                return _usdt_l1_unpack(payload)
        pack = _usdt_parallel_worker(branch)
        if unified_doc is not None:
            ucc.l1_set(
                unified_doc,
                l1_key,
                _usdt_l1_pack(pack),
                ttl_sec=ucc.TTL_L1_USDT_BRANCH_SEC,
            )
        return pack

    for _key, pack, exc in map_bounded(
        list(_USDT_BRANCH_KEYS),
        _work,
        max_workers=parallel_max_workers,
    ):
        if exc is not None:
            raise exc
        assert pack is not None
        rpart, tpart, wpart = pack
        warnings.extend(wpart)
        rub.update(rpart)
        thb.update(tpart)

    deps: Dict[str, int] = {}
    if unified_doc is not None:
        l1 = unified_doc.get("l1") or {}
        for branch in _USDT_BRANCH_KEYS:
            k = f"usdt:l1:{branch}"
            ent = l1.get(k)
            if isinstance(ent, dict) and int(ent.get("version", 0)) > 0:
                deps[k] = int(ent["version"])

    data = {"rub_per_usdt": rub, "thb_per_usdt": thb}
    return data, warnings, deps


def _empty_usdt_data() -> Dict[str, Any]:
    return {
        "rub_per_usdt": {
            "bybit_cash": None,
            "bybit_transfer": None,
            "htx_cash": None,
            "htx_no_cash": None,
        },
        "thb_per_usdt": {
            "bitkub_highest_bid": None,
            "binance_bid": None,
            "fly_bid": None,
            "it_obmen_bid": None,
        },
    }


def compute_usdt_report(
    *,
    refresh: bool,
    cache_file: Optional[Path] = None,
    unified_allow_stale: bool = False,
    readonly: bool = False,
) -> Tuple[Dict[str, Any], List[str]]:
    global _unified_served_stale_l2

    path = cache_file if cache_file is not None else USDT_CACHE_FILE
    key = _usdt_cache_key()
    l2_key = "l2:usdt:default"
    from_stale_l2 = False

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    ucc.migrate_legacy_usdt_cache(
        doc, legacy_path=path, usdt_key=key, cache_version=USDT_CACHE_VERSION
    )

    allow_stale_l2 = bool(unified_allow_stale or readonly)

    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_USDT_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and allow_stale_l2:
            ent = ucc.l2_get(
                doc,
                l2_key,
                ttl_sec=ucc.TTL_L2_USDT_SEC,
                require_fresh=False,
                allow_stale=True,
            )
            if ent is not None:
                from_stale_l2 = True
        if ent is not None:
            deps = ent.get("deps") or {}
            payload = ent.get("payload") or {}
            data = payload.get("data") or {}
            if data:
                match = (not deps) or ucc.l2_deps_match(doc, deps)
                if match or readonly:
                    w = list(payload.get("warnings", []))
                    if readonly and not match:
                        w.append(
                            "readonly: L2 USDT при несовпадении deps — только снимок из кеша."
                        )
                    _unified_served_stale_l2 = from_stale_l2
                    return dict(data), w

    _unified_served_stale_l2 = False

    if readonly:
        hit = _load_stale_usdt_cache(path)
        if hit is not None:
            raw, _saved = hit
            if raw.get("key") == key:
                data = raw.get("data") or {}
                if isinstance(data, dict) and data:
                    return dict(data), list(raw.get("warnings", []))
        return _empty_usdt_data(), [
            "--readonly: нет USDT в unified L2 и нет подходящей записи в legacy-файле кеша."
        ]

    data, warnings, deps = fetch_usdt_payload(
        unified_doc=doc,
        refresh=refresh,
    )
    text = format_usdt_report_text(data, warnings)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_USDT_SEC,
        text=text,
        deps=deps,
        payload={"data": data, "warnings": warnings},
    )
    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    save_obj = {
        "v": USDT_CACHE_VERSION,
        "saved_unix": time.time(),
        "key": key,
        "data": data,
        "warnings": warnings,
    }
    try:
        path.write_text(json.dumps(save_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        warnings.append(f"Не удалось записать кеш USDT: {path} ({e})")

    return data, warnings


def _fmt_pipe_value(x: Optional[float]) -> str:
    """Левая колонка в тексте: два знака после точки или «—»."""
    if x is None or x <= 0:
        return "—"
    return f"{x:.2f}"


def _cross_rub_thb(rub_u: Optional[float], thb_u: Optional[float]) -> Optional[float]:
    if rub_u is None or thb_u is None or rub_u <= 0 or thb_u <= 0:
        return None
    return rub_u / thb_u


def _sort_pipe_rows_asc(rows: List[Tuple[str, Optional[float]]]) -> List[Tuple[str, Optional[float]]]:
    """По возрастанию курса; без числа («—») — в конце; при равенстве — по подписи."""

    def sort_key(item: Tuple[str, Optional[float]]) -> Tuple[float, str]:
        lab, v = item
        if v is None or v <= 0:
            return (float("inf"), lab)
        return (v, lab)

    return sorted(rows, key=sort_key)


def _sort_pipe_rows_desc(rows: List[Tuple[str, Optional[float]]]) -> List[Tuple[str, Optional[float]]]:
    """По убыванию курса; без числа («—») — в конце; при равенстве — по подписи."""

    def sort_key(item: Tuple[str, Optional[float]]) -> Tuple[int, float, str]:
        lab, v = item
        if v is None or v <= 0:
            return (1, 0.0, lab)
        return (0, -v, lab)

    return sorted(rows, key=sort_key)


def _pipe_lines(rows: List[Tuple[str, Optional[float]]]) -> List[str]:
    """Строки вида ``  79.50 | Bybit (наличные)`` — курс слева (два знака), подпись справа."""
    if not rows:
        return []
    cells = [(_fmt_pipe_value(v), lab) for lab, v in rows]
    w = max(len(s) for s, _ in cells)
    return [f"  {s:>{w}} | {lab}" for s, lab in cells]


def format_usdt_report_text(data: Dict[str, Any], warnings: List[str]) -> str:
    rub = data.get("rub_per_usdt") or {}
    thb = data.get("thb_per_usdt") or {}
    bk = thb.get("bitkub_highest_bid")
    bn = thb.get("binance_bid")
    fly = thb.get("fly_bid")
    it_obmen = thb.get("it_obmen_bid")

    rub_rows: List[Tuple[str, Optional[float]]] = [
        ("Bybit (наличные)", float(rub["bybit_cash"]) if isinstance(rub.get("bybit_cash"), (int, float)) else None),
        ("Bybit (перевод)", float(rub["bybit_transfer"]) if isinstance(rub.get("bybit_transfer"), (int, float)) else None),
        ("HTX (наличные)", float(rub["htx_cash"]) if isinstance(rub.get("htx_cash"), (int, float)) else None),
        ("HTX (перевод)", float(rub["htx_no_cash"]) if isinstance(rub.get("htx_no_cash"), (int, float)) else None),
    ]
    thb_rows: List[Tuple[str, Optional[float]]] = [
        ("Bitkub (highestBid)", float(bk) if isinstance(bk, (int, float)) and bk and bk > 0 else None),
        ("Binance TH (bid)", float(bn) if isinstance(bn, (int, float)) and bn and bn > 0 else None),
        ("Fly Currency (минимальная сумма)", float(fly) if isinstance(fly, (int, float)) and fly and fly > 0 else None),
        ("IT Обмен (до 1000 USDT)", float(it_obmen) if isinstance(it_obmen, (int, float)) and it_obmen and it_obmen > 0 else None),
    ]

    lines: List[str] = [
        "Отчёт USDT: P2P RUB/USDT и USDT/THB.",
        "",
        "RUB за 1 USDT (P2P, лучшая цена)",
        *_pipe_lines(_sort_pipe_rows_asc(rub_rows)),
        "",
        "THB за 1 USDT",
        *_pipe_lines(_sort_pipe_rows_desc(thb_rows)),
        "",
        "Полные пути: RUB за 1 THB (P2P × площадка TH)",
    ]

    paths = [
        ("Bybit P2P (наличные) → Bitkub", rub.get("bybit_cash"), bk),
        ("Bybit P2P (наличные) → Binance TH", rub.get("bybit_cash"), bn),
        ("Bybit P2P (перевод) → Bitkub", rub.get("bybit_transfer"), bk),
        ("Bybit P2P (перевод) → Binance TH", rub.get("bybit_transfer"), bn),
        ("HTX P2P (наличные) → Bitkub", rub.get("htx_cash"), bk),
        ("HTX P2P (наличные) → Binance TH", rub.get("htx_cash"), bn),
        ("HTX P2P (перевод) → Bitkub", rub.get("htx_no_cash"), bk),
        ("HTX P2P (перевод) → Binance TH", rub.get("htx_no_cash"), bn),
    ]
    path_rows: List[Tuple[str, Optional[float]]] = []
    for label, rpu, tpu in paths:
        rpv = float(rpu) if isinstance(rpu, (int, float)) else None
        tpv = float(tpu) if isinstance(tpu, (int, float)) else None
        cr = _cross_rub_thb(rpv, tpv)
        path_rows.append((label, cr))
    lines.extend(_pipe_lines(_sort_pipe_rows_asc(path_rows)))

    if warnings:
        lines.append("")
        lines.append("Предупреждения:")
        for w in warnings:
            lines.append(f"  • {w}")

    return "\n".join(lines) + "\n"


def print_usdt_report_json(data: Dict[str, Any], warnings: List[str], file: TextIO) -> None:
    out: Dict[str, Any] = {
        "rub_per_usdt": data.get("rub_per_usdt"),
        "thb_per_usdt": data.get("thb_per_usdt"),
        "full_paths_rub_per_thb": [],
        "warnings": warnings,
    }
    rub = data.get("rub_per_usdt") or {}
    thb = data.get("thb_per_usdt") or {}
    bk = thb.get("bitkub_highest_bid")
    bn = thb.get("binance_bid")
    fly = thb.get("fly_bid")
    it_obmen = thb.get("it_obmen_bid")
    paths = [
        ("Bybit P2P (наличные) → Bitkub", rub.get("bybit_cash"), bk),
        ("Bybit P2P (наличные) → Binance TH", rub.get("bybit_cash"), bn),
        ("Bybit P2P (перевод) → Bitkub", rub.get("bybit_transfer"), bk),
        ("Bybit P2P (перевод) → Binance TH", rub.get("bybit_transfer"), bn),
        ("HTX P2P (наличные) → Bitkub", rub.get("htx_cash"), bk),
        ("HTX P2P (наличные) → Binance TH", rub.get("htx_cash"), bn),
        ("HTX P2P (перевод) → Bitkub", rub.get("htx_no_cash"), bk),
        ("HTX P2P (перевод) → Binance TH", rub.get("htx_no_cash"), bn),
    ]
    full_paths: List[Dict[str, Any]] = []
    for label, rpu, tpu in paths:
        rc = rpu if isinstance(rpu, (int, float)) else None
        tc = tpu if isinstance(tpu, (int, float)) else None
        cr = _cross_rub_thb(rc, tc)
        full_paths.append(
            {"label": label, "rub_per_thb": None if cr is None else round(cr, 2)},
        )
    full_paths.sort(
        key=lambda d: (
            float("inf") if d["rub_per_thb"] is None else d["rub_per_thb"],
            d["label"],
        ),
    )
    out["full_paths_rub_per_thb"] = full_paths
    print(json.dumps(out, ensure_ascii=False, indent=2), file=file)


def usdt_subcommand_help() -> str:
    return (
        "usdt — отчёт P2P RUB/USDT и USDT/THB (Bitkub, Binance TH); "
        "кеш в RATES_USDT_CACHE_FILE или .rates_usdt_cache.json (TTL 1 мин); "
        "опции: --refresh, --json, --cache-file <путь>.\n"
        "  Параллельные блоки API: RATES_PARALLEL_MAX_WORKERS."
    )
