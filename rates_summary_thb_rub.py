#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводка курсов **RUB за 1 THB** (направление **RUB → THB**: сколько рублей отдаёте за бат).

Источники подключаются через :mod:`rates_sources`: у каждого своя функция ``fetch(ctx)``,
возвращающая список котировок (курс + метка). **Первым всегда идёт Forex** — база для %%.

Пример::

    python rates_summary_thb_rub.py
    python rates_summary_thb_rub.py --refresh --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import koronapay_tariffs as _korona_ref

from rates_sources import RateRow, collect_rows

CACHE_FILE = _SCRIPT_DIR / ".rates_summary_cache.json"
CACHE_TTL_SEC = 30 * 60
CACHE_VERSION = 15


def _cache_key(params: Dict[str, Any]) -> Dict[str, Any]:
    return {"v": CACHE_VERSION, "params": params}


def load_stale_cache(path: Path) -> Optional[Tuple[Dict[str, Any], float]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if raw.get("v") != CACHE_VERSION:
        return None
    saved = float(raw.get("saved_unix", 0))
    return raw, saved


def cache_valid(raw: Dict[str, Any], saved: float, key: Dict[str, Any]) -> bool:
    if time.time() - saved > CACHE_TTL_SEC:
        return False
    return raw.get("key") == key


def rows_from_cached(raw: Dict[str, Any]) -> Tuple[List[RateRow], float]:
    rows = [RateRow(**r) for r in raw.get("rows", [])]
    baseline = float(raw.get("baseline", 0))
    return rows, baseline


# Референсные суммы (как в fx_reports / ваших примерах)
DEFAULT_THB_REF = 30_000.0
DEFAULT_ATM_FEE_THB = 250.0
DEFAULT_KORONA_LARGE_THB = 40_000.0
DEFAULT_KORONA_SMALL_RUB = float(_korona_ref.RUB_MIN_SENDING_FOR_BEST_TIER) - 1.0
DEFAULT_AVOSEND_RUB = 10_000.0


def main() -> int:
    p = argparse.ArgumentParser(description="Сводка RUB/THB из скриптов проекта (кеш 30 мин)")
    p.add_argument("--refresh", action="store_true", help="Игнорировать кеш")
    p.add_argument("--json", action="store_true", help="JSON в stdout")
    p.add_argument("--thb-ref", type=float, default=DEFAULT_THB_REF, help="Нетто THB для сценариев снятия")
    p.add_argument("--atm-fee", type=float, default=DEFAULT_ATM_FEE_THB, help="Комиссия банкомата, THB")
    p.add_argument("--korona-small", type=float, default=DEFAULT_KORONA_SMALL_RUB)
    p.add_argument(
        "--korona-large-thb",
        type=float,
        default=DEFAULT_KORONA_LARGE_THB,
        help="Сумма получения THB для строки Korona (крупная)",
    )
    p.add_argument("--avosend-rub", type=float, default=DEFAULT_AVOSEND_RUB)
    p.add_argument("--unionpay-date", default=None, help="YYYY-MM-DD для JSON UnionPay")
    p.add_argument("--moex-override", type=float, default=None)
    p.add_argument(
        "--cache-file",
        type=Path,
        default=CACHE_FILE,
        help="Файл кеша",
    )
    args = p.parse_args()

    key_params = {
        "thb_ref": args.thb_ref,
        "atm_fee": args.atm_fee,
        "korona_small": args.korona_small,
        "korona_large_thb": args.korona_large_thb,
        "avosend_rub": args.avosend_rub,
        "unionpay_date": args.unionpay_date,
        "moex_override": args.moex_override,
    }
    cache_key = _cache_key(key_params)

    rows: List[RateRow] = []
    baseline = 0.0
    warnings: List[str] = []

    if not args.refresh:
        hit = load_stale_cache(args.cache_file)
        if hit is not None:
            raw, saved = hit
            if cache_valid(raw, saved, cache_key):
                rows, baseline = rows_from_cached(raw)
                warnings = list(raw.get("warnings", []))

    if not rows:
        rows, baseline, warnings = collect_rows(
            thb_ref=args.thb_ref,
            atm_fee=args.atm_fee,
            korona_small_rub=args.korona_small,
            korona_large_thb=args.korona_large_thb,
            avosend_rub=args.avosend_rub,
            unionpay_date=args.unionpay_date,
            moex_override=args.moex_override,
        )
        bl = next((r.rate for r in rows if r.is_baseline), baseline)
        save_payload = {
            "v": CACHE_VERSION,
            "saved_unix": time.time(),
            "key": cache_key,
            "baseline": bl,
            "rows": [asdict(r) for r in rows],
            "warnings": warnings,
        }
        try:
            args.cache_file.write_text(
                json.dumps(save_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            warnings.append(f"Не удалось записать кеш: {args.cache_file}")

    baseline = next((r.rate for r in rows if r.is_baseline), baseline)
    if baseline <= 0 and rows:
        baseline = min(r.rate for r in rows)

    if args.json:
        out = {
            "baseline_rub_per_thb": baseline,
            "rows": [asdict(r) for r in rows],
            "warnings": warnings,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("RUB ➔ THB")
    print()
    for r in rows:
        print(r.format_line(baseline))
    if warnings:
        print()
        print("Предупреждения:")
        for w in warnings:
            print(f"  • {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
