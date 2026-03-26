#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводка курсов **RUB за 1 THB** (направление **RUB → THB**: сколько рублей отдаёте за бат).

Источники — пакет :mod:`sources`; реестр в :mod:`rates_sources`. **Первым всегда Forex** — база для %%.

Файл ``.env`` в каталоге с ``rates.py`` подхватывается при запуске (см. ``env_loader``; ``export`` в shell имеет приоритет).

Пример::

    python rates.py
    python rates.py --refresh --json
    python rates.py --help
    python rates.py sources
    python rates.py save out.txt
    python rates.py usdt [--refresh]
    python rates.py unired_bkb summary
    python rates.py unired_bkb --refresh
    python rates.py forex --help
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from env_loader import load_repo_dotenv

load_repo_dotenv(_SCRIPT_DIR)

from rates_sources import (
    FetchContext,
    RateRow,
    SourceCategory,
    collect_rows,
    is_cash_category,
)
from sources.korona.koronapay_tariffs import RUB_MIN_SENDING_FOR_BEST_TIER
from sources import plugin_by_id, registered_source_ids

_CACHE_OVERRIDE = (os.environ.get("RATES_CACHE_FILE") or "").strip()
CACHE_FILE = Path(_CACHE_OVERRIDE) if _CACHE_OVERRIDE else _SCRIPT_DIR / ".rates_summary_cache.json"
CACHE_TTL_SEC = 30 * 60
CACHE_VERSION = 32

_RESERVED = frozenset(
    {"sources", "save", "usdt", "env-status", "cash", "cash-thb", "exchange"}
)


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


def _row_from_cache_dict(r: Dict[str, Any]) -> RateRow:
    d = dict(r)
    cat = d.get("category")
    if isinstance(cat, str):
        if cat == "CASH":
            cat = "CASH_RUB"
        try:
            d["category"] = SourceCategory[cat]
        except KeyError:
            d["category"] = SourceCategory.TRANSFER
    elif cat is None:
        d["category"] = SourceCategory.TRANSFER
    if "compare_to_baseline" not in d:
        d["compare_to_baseline"] = True
    if "cash_rub_seq" not in d:
        d["cash_rub_seq"] = 0
    return RateRow(**d)


def rows_from_cached(raw: Dict[str, Any]) -> Tuple[List[RateRow], float]:
    rows = [_row_from_cache_dict(r) for r in raw.get("rows", [])]
    baseline = float(raw.get("baseline", 0))
    return rows, baseline


# Референсные суммы (как в fx_reports / ваших примерах)
DEFAULT_THB_REF = 30_000.0
DEFAULT_ATM_FEE_THB = 250.0
DEFAULT_KORONA_LARGE_THB = 40_000.0
DEFAULT_KORONA_SMALL_RUB = float(RUB_MIN_SENDING_FOR_BEST_TIER) - 1.0
DEFAULT_AVOSEND_RUB = 50_000.0


def build_arg_parser(*, add_help: bool = True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Сводка RUB/THB из скриптов проекта (кеш 30 мин)",
        add_help=add_help,
    )
    if not add_help:
        p.add_argument("-h", "--help", action="store_true", help=argparse.SUPPRESS)
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
    return p


def _row_cache_dict(row: RateRow) -> Dict[str, Any]:
    d = asdict(row)
    d["category"] = row.category.name
    return d


def compute_summary_rows(args: argparse.Namespace) -> Tuple[List[RateRow], float, List[str]]:
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
            "rows": [_row_cache_dict(r) for r in rows],
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

    return rows, baseline, warnings


def _cash_section_title(cat: SourceCategory) -> str:
    return {
        SourceCategory.CASH_RUB: "Наличные RUB ➔ THB",
        SourceCategory.CASH_USD: "Наличные USD ➔ THB",
        SourceCategory.CASH_EUR: "Наличные EUR ➔ THB",
        SourceCategory.CASH_CNY: "Наличные CNY ➔ THB",
    }.get(cat, "Наличные")


def print_summary_text(rows: List[RateRow], baseline: float, warnings: List[str], file: TextIO) -> None:
    print("Перевод RUB ➔ THB", file=file)
    print(file=file)
    prev_cat: Optional[SourceCategory] = None
    for r in rows:
        if is_cash_category(r.category):
            new_block = (
                prev_cat is None
                or not is_cash_category(prev_cat)
                or r.category != prev_cat
            )
            if new_block:
                if prev_cat is not None:
                    print(file=file)
                print(_cash_section_title(r.category), file=file)
        print(r.format_line(baseline), file=file)
        prev_cat = r.category
    if warnings:
        print(file=file)
        print("Предупреждения:", file=file)
        for w in warnings:
            print(f"  • {w}", file=file)


def print_json_summary(rows: List[RateRow], baseline: float, warnings: List[str], file: TextIO) -> None:
    out = {
        "baseline_rub_per_thb": baseline,
        "rows": [{**asdict(r), "category": r.category.name} for r in rows],
        "warnings": warnings,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2), file=file)


def _fetch_context_from_summary_args(args: argparse.Namespace) -> FetchContext:
    """Тот же контекст, что у :func:`collect_rows`, для вызова ``plugin.summary(ctx)``."""
    return FetchContext(
        thb_ref=args.thb_ref,
        atm_fee=args.atm_fee,
        korona_small_rub=args.korona_small,
        korona_large_thb=args.korona_large_thb,
        avosend_rub=args.avosend_rub,
        unionpay_date=args.unionpay_date,
        moex_override=args.moex_override,
        warnings=[],
    )


def print_single_source_summary(mod: Any, args: argparse.Namespace) -> int:
    """Одна или несколько котировок источника + предупреждения (без кеша полной сводки)."""
    ctx = _fetch_context_from_summary_args(args)
    quotes = mod.summary(ctx)
    lines: List[str] = []
    if quotes:
        for q in quotes:
            row = RateRow(
                rate=q.rate,
                label=q.label,
                emoji=q.emoji or mod.EMOJI,
                note=q.note,
                is_baseline=False,
                category=q.category or mod.CATEGORY,
                compare_to_baseline=False,
                cash_rub_seq=q.cash_rub_seq,
                merge_key=q.merge_key,
            )
            lines.append(row.format_line(0.0))
    else:
        lines.append("(нет котировок)")
    if ctx.warnings:
        lines.append("")
        lines.append("Предупреждения:")
        for w in ctx.warnings:
            lines.append(f"  • {w}")
    print("\n".join(lines))
    return 0


def _print_single_source_summary_usage(stream: TextIO, source_id: str) -> None:
    print(
        f"Использование: rates.py [общие опции] {source_id} summary [--refresh]\n\n"
        "Запрос только этого источника. Учитываются общие параметры сводки: "
        "--thb-ref, --atm-fee, --korona-small, --korona-large-thb, --avosend-rub, "
        "--unionpay-date, --moex-override. Кеш полной сводки (.rates_summary_cache) "
        "не используется; --refresh в хвосте summary допускается для единообразия с CLI.",
        file=stream,
    )


def print_global_help(parser: argparse.ArgumentParser) -> None:
    parser.print_help()
    print("\nКоманды:")
    print("  sources              Список id доступных источников.")
    print("  env-status           Файл .env и типичные переменные (без значений).")
    print("  save <файл>          Записать текстовую сводку в файл (те же опции, что и для сводки).")
    print("  usdt [--refresh] [--json] [--cache-file ПУТЬ]  Отчёт P2P RUB/USDT и USDT/THB (отдельный кеш).")
    print(
        "  cash [--top N] [--no-banki] [--refresh]     Курсы продажи наличной валюты (РБК+Banki.ru)."
    )
    print(
        "  cash-thb [--top N] [--no-banki] [--refresh]  То же × TT Exchange → RUB/THB (см. help)."
    )
    print(
        "  exchange [--top N] [--lang ru]   Топ филиалов TT (USD/EUR/CNY→THB) + строка Ex24."
    )
    print("  <source_id> summary [--refresh]  Только этот источник (те же --korona-*, --avosend-rub, …).")
    print("  <source_id> --refresh          То же, если других аргументов у id нет.")
    print("  <source_id> [args]   Иные подкоманды источника (см. python ... <id> --help).")
    print(
        "\nПараллельные запросы: переменная RATES_PARALLEL_MAX_WORKERS (сводка источников, "
        "cash, cash-thb, usdt, exchange; по умолчанию см. rates_parallel)."
    )
    print("\nИсточники (кратко; полное: <id> --help):")
    for sid in registered_source_ids():
        mod = plugin_by_id(sid)
        if mod is None:
            continue
        ht = mod.help_text().strip().replace("\n", " ")
        print(f"  {sid}")
        print(f"      {ht}")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser(add_help=False)
    args, rest = parser.parse_known_args(argv)
    source_ids = frozenset(registered_source_ids())

    if getattr(args, "help", False):
        if len(rest) >= 2 and rest[0] in source_ids and rest[1] == "summary":
            _print_single_source_summary_usage(sys.stdout, rest[0])
            return 0
        if len(rest) >= 1 and rest[0] in source_ids:
            mod = plugin_by_id(rest[0])
            if mod is None:
                print(f"Нет модуля для источника {rest[0]!r}", file=sys.stderr)
                return 2
            tail = list(rest[1:]) + ["--help"]
            return mod.command(tail)
        if len(rest) >= 1 and rest[0] == "sources":
            print("sources — вывести список id зарегистрированных источников курса.")
            return 0
        if len(rest) >= 1 and rest[0] == "env-status":
            print("env-status — проверить наличие .env и типичных переменных окружения.")
            return 0
        if len(rest) >= 1 and rest[0] == "save":
            print("save <файл> — записать сводку в файл (опции --json, --refresh и др. как у обычного запуска).")
            return 0
        if len(rest) >= 1 and rest[0] == "cash":
            import cash_report as _cr

            print(_cr.cash_subcommand_help())
            return 0
        if len(rest) >= 1 and rest[0] == "cash-thb":
            import cash_report as _cr

            print(_cr.cash_thb_subcommand_help())
            return 0
        if len(rest) >= 1 and rest[0] == "exchange":
            import exchange_report as _er

            print(_er.exchange_subcommand_help())
            return 0
        if len(rest) >= 1 and rest[0] == "usdt":
            import usdt_report as _ur

            print(_ur.usdt_subcommand_help())
            return 0
        print_global_help(build_arg_parser(add_help=True))
        return 0

    if not rest:
        rows, baseline, warnings = compute_summary_rows(args)
        if args.json:
            print_json_summary(rows, baseline, warnings, sys.stdout)
        else:
            print_summary_text(rows, baseline, warnings, sys.stdout)
        return 0

    head = rest[0]
    if head in ("--help", "-h"):
        print_global_help(build_arg_parser(add_help=True))
        return 0

    if head == "sources":
        if any(x in ("--help", "-h") for x in rest[1:]):
            print("sources — вывести список id зарегистрированных источников курса.")
            return 0
        for sid in registered_source_ids():
            print(sid)
        return 0

    if head == "env-status":
        from env_loader import ENV_STATUS_KEYS

        dotenv_path = _SCRIPT_DIR / ".env"
        ok_file = dotenv_path.is_file()
        print(f"Файл {dotenv_path}: {'найден' if ok_file else 'нет'}")
        print(
            "При старте rates.py и bot уже вызван load_repo_dotenv (.env → os.environ.setdefault; "
            "уже заданные в shell переменные не меняются)."
        )
        print("Типичные ключи (только факт наличия в os.environ):")
        for k in ENV_STATUS_KEYS:
            set_yes = bool((os.environ.get(k) or "").strip())
            print(f"  {k}: {'задано' if set_yes else 'нет'}")
        return 0

    if head == "save":
        if len(rest) < 2:
            print("save: укажите имя файла, например: save out.txt", file=sys.stderr)
            return 2
        out_path = Path(rest[1])
        tail = rest[2:]
        if tail:
            args2, unk = parser.parse_known_args(tail)
            if unk:
                print(f"Неизвестные аргументы: {' '.join(unk)}", file=sys.stderr)
                return 2
            for k, v in vars(args2).items():
                setattr(args, k, v)
        rows, baseline, warnings = compute_summary_rows(args)
        try:
            with out_path.open("w", encoding="utf-8") as f:
                if args.json:
                    print_json_summary(rows, baseline, warnings, f)
                else:
                    print_summary_text(rows, baseline, warnings, f)
        except OSError as e:
            print(f"Не удалось записать файл: {e}", file=sys.stderr)
            return 1
        return 0

    if head == "cash":
        import cash_report as cr

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(cr.cash_subcommand_help())
            return 0
        tail = rest[1:]
        err = cr.main_cash_cli(tail)
        return err

    if head == "cash-thb":
        import cash_report as cr

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(cr.cash_thb_subcommand_help())
            return 0
        tail = rest[1:]
        err = cr.main_cash_thb_cli(tail)
        return err

    if head == "exchange":
        import exchange_report as er

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(er.exchange_subcommand_help())
            return 0
        tail = rest[1:]
        err = er.main_exchange_cli(tail)
        return err

    if head == "usdt":
        import usdt_report as ur

        if any(x in ("--help", "-h") for x in rest[1:]):
            print(ur.usdt_subcommand_help())
            return 0
        u_parser = argparse.ArgumentParser(add_help=False)
        u_parser.add_argument("--refresh", action="store_true")
        u_parser.add_argument("--json", action="store_true")
        u_parser.add_argument("--cache-file", type=Path, default=ur.USDT_CACHE_FILE)
        u_args, u_rest = u_parser.parse_known_args(rest[1:])
        if u_rest:
            print(f"Неизвестные аргументы usdt: {' '.join(u_rest)}", file=sys.stderr)
            return 2
        # --refresh / --json до или после ``usdt`` обрабатываются общим парсером и не попадают в rest.
        refresh = u_args.refresh or args.refresh
        as_json = u_args.json or args.json
        data, uw = ur.compute_usdt_report(refresh=refresh, cache_file=u_args.cache_file)
        if as_json:
            ur.print_usdt_report_json(data, uw, sys.stdout)
        else:
            print(ur.format_usdt_report_text(data, uw), end="")
        return 0

    if head in source_ids:
        mod = plugin_by_id(head)
        if mod is None:
            print(f"Внутренняя ошибка: нет модуля для {head!r}", file=sys.stderr)
            return 2
        tail = rest[1:]
        want_summary = False
        if tail and tail[0] == "summary":
            want_summary = True
            if any(x in ("-h", "--help") for x in tail[1:]):
                _print_single_source_summary_usage(sys.stdout, head)
                return 0
            for x in tail[1:]:
                if x == "--refresh":
                    continue
                print(f"Неизвестный аргумент после summary: {x}", file=sys.stderr)
                return 2
        elif args.refresh and not tail:
            want_summary = True
        if want_summary:
            return print_single_source_summary(mod, args)
        return mod.command(tail)

    if head in _RESERVED:
        print(f"Зарезервированная команда {head!r} уже обработана — внутренняя ошибка.", file=sys.stderr)
        return 2

    print(f"Неизвестная команда или источник: {head!r}", file=sys.stderr)
    print(
        "Подсказка: --help, sources, save <файл>, usdt, cash, cash-thb, exchange или id источника.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
