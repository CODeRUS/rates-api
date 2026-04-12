#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводка курсов **RUB за 1 THB** (направление **RUB → THB**: сколько рублей отдаёте за бат).

Источники — пакет :mod:`sources`; реестр в :mod:`rates_sources`. **Первым всегда Forex** — база для %%.

Файл ``.env`` в каталоге с ``rates.py`` подхватывается при запуске (см. ``env_loader``; ``export`` в shell имеет приоритет).

Пример::

    python rates.py
    python rates.py --refresh --json
    python rates.py --readonly
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
import io
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

import rates_unified_cache as ucc

from rates_sources import (
    FetchContext,
    RateRow,
    SourceCategory,
    collect_rows,
    is_cash_category,
    run_sources_unified,
)
from sources.korona.koronapay_tariffs import RUB_MIN_SENDING_FOR_BEST_TIER
from sources import plugin_by_id, registered_source_ids

from rates_output_filters import apply_summary_row_filter

# Не показывать в текстовой сводке (CLI, save, бот) блоки наличных USD/E/CNY→THB.
_SUMMARY_OMIT_CASH_FX = frozenset(
    (SourceCategory.CASH_USD, SourceCategory.CASH_EUR, SourceCategory.CASH_CNY)
)

_CACHE_OVERRIDE = (os.environ.get("RATES_CACHE_FILE") or "").strip()
_CACHE_OVERRIDE_PATH = Path(_CACHE_OVERRIDE) if _CACHE_OVERRIDE else None
if _CACHE_OVERRIDE_PATH is not None and not _CACHE_OVERRIDE_PATH.is_absolute():
    _CACHE_OVERRIDE_PATH = (_SCRIPT_DIR / _CACHE_OVERRIDE_PATH).resolve()
CACHE_FILE = (
    _CACHE_OVERRIDE_PATH if _CACHE_OVERRIDE_PATH is not None else _SCRIPT_DIR / ".rates_summary_cache.json"
)
CACHE_TTL_SEC = 30 * 60
CACHE_VERSION = 32

_RESERVED = frozenset(
    {"sources", "save", "usdt", "env-status", "cash", "exchange", "rshb", "calc"}
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
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--refresh", action="store_true", help="Игнорировать кеш")
    grp.add_argument(
        "--readonly",
        action="store_true",
        help=(
            "Без сетевых запросов: только данные из unified- и файловых кешей "
            "(в т.ч. L2 с истёкшим TTL). Несовместимо с --refresh."
        ),
    )
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
    p.add_argument(
        "--filter",
        dest="output_filter",
        default="",
        metavar="NAME",
        help="Пресет постфильтрации вывода (например travelask). Неизвестное имя — без эффекта.",
    )
    p.add_argument(
        "--gpt",
        dest="gpt_prompt",
        default=None,
        metavar="PROMPT",
        help="Запрос к OpenAI Chat: OPENAI_API_KEY, OPENAI_API_URL; опц. OPENAI_PROMPT, OPENAI_MODEL, OPENAI_HTTP_TIMEOUT_SEC.",
    )
    return p


def _row_cache_dict(row: RateRow) -> Dict[str, Any]:
    d = asdict(row)
    d["category"] = row.category.name
    return d


def _summary_rows_from_l2_payload(payload: Dict[str, Any]) -> Tuple[List[RateRow], float, List[str]]:
    rows = [_row_from_cache_dict(r) for r in payload.get("rows", [])]
    baseline = float(payload.get("baseline", 0))
    warnings = list(payload.get("warnings", []))
    return rows, baseline, warnings


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
    readonly = bool(getattr(args, "readonly", False))
    allow_stale = bool(
        getattr(args, "unified_allow_stale", False) or readonly
    )
    digest = ucc.stable_digest(cache_key)
    l2_key = f"l2:summary:{digest}"
    from_stale_l2 = False
    rebuilt = False

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    if ucc.migrate_legacy_summary_cache(
        doc,
        legacy_path=args.cache_file,
        cache_key=cache_key,
        cache_version=CACHE_VERSION,
        ttl_sec=ucc.TTL_L2_SUMMARY_SEC,
    ):
        try:
            ucc.save_unified(doc, unified_path)
        except OSError:
            pass

    rows: List[RateRow] = []
    baseline = 0.0
    warnings: List[str] = []

    if not args.refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_SUMMARY_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and allow_stale:
            ent = ucc.l2_get(
                doc,
                l2_key,
                ttl_sec=ucc.TTL_L2_SUMMARY_SEC,
                require_fresh=False,
                allow_stale=True,
            )
            if ent is not None:
                from_stale_l2 = True
        if ent is not None:
            deps = ent.get("deps") or {}
            payload = ent.get("payload") or {}
            if payload.get("rows") is not None:
                dep_ok = (not deps) or ucc.l2_deps_match(doc, deps)
                if dep_ok or readonly:
                    rows, baseline, warnings = _summary_rows_from_l2_payload(payload)
                    if readonly and not dep_ok:
                        warnings.append(
                            "readonly: L2 сводка при несовпадении deps — только снимок из кеша."
                        )

    if not rows and not args.refresh:
        hit = load_stale_cache(args.cache_file)
        if hit is not None:
            raw, saved = hit
            if cache_valid(raw, saved, cache_key) or (
                readonly and raw.get("key") == cache_key
            ):
                rows, baseline = rows_from_cached(raw)
                warnings = list(raw.get("warnings", []))

    deps: Dict[str, int] = {}
    if not rows:
        if readonly:
            return (
                [],
                0.0,
                warnings
                + [
                    "--readonly: нет сводки в unified L2 и нет подходящей записи в файле legacy-кеша."
                ],
            )
        rebuilt = True
        ctx = FetchContext(
            thb_ref=args.thb_ref,
            atm_fee=args.atm_fee,
            korona_small_rub=args.korona_small,
            korona_large_thb=args.korona_large_thb,
            avosend_rub=args.avosend_rub,
            unionpay_date=args.unionpay_date,
            moex_override=args.moex_override,
            warnings=[],
        )
        rows, baseline, warnings, deps = run_sources_unified(
            ctx,
            doc,
            digest,
            refresh=args.refresh,
            sources=None,
            parallel_max_workers=None,
        )
        bl = next((r.rate for r in rows if r.is_baseline), baseline)
        buf = io.StringIO()
        print_summary_text(rows, bl, warnings, buf)
        text = buf.getvalue()
        ucc.l2_set(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_SUMMARY_SEC,
            text=text,
            deps=deps,
            payload={
                "rows": [_row_cache_dict(r) for r in rows],
                "baseline": bl,
                "warnings": warnings,
            },
        )
        try:
            ucc.save_unified(doc, unified_path)
        except OSError as e:
            warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")
        legacy_payload = {
            "v": CACHE_VERSION,
            "saved_unix": time.time(),
            "key": cache_key,
            "baseline": bl,
            "rows": [_row_cache_dict(r) for r in rows],
            "warnings": warnings,
        }
        try:
            args.cache_file.write_text(
                json.dumps(legacy_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            warnings.append(f"Не удалось записать кеш: {args.cache_file}")

    baseline = next((r.rate for r in rows if r.is_baseline), baseline)
    if baseline <= 0 and rows:
        baseline = min(r.rate for r in rows)

    setattr(
        args,
        "_unified_served_stale_l2",
        bool(from_stale_l2 and rows and not rebuilt),
    )

    return rows, baseline, warnings


def _maybe_apply_output_filter(
    args: argparse.Namespace, rows: List[RateRow]
) -> List[RateRow]:
    fn = (getattr(args, "output_filter", None) or "").strip()
    if not fn:
        return rows
    return apply_summary_row_filter(rows, fn)


def _cash_section_title(cat: SourceCategory) -> str:
    return {
        SourceCategory.CASH_RUB: "Наличные RUB ➔ THB",
        SourceCategory.CASH_USD: "Наличные USD ➔ THB",
        SourceCategory.CASH_EUR: "Наличные EUR ➔ THB",
        SourceCategory.CASH_CNY: "Наличные CNY ➔ THB",
    }.get(cat, "Наличные")


def print_summary_text(rows: List[RateRow], baseline: float, warnings: List[str], file: TextIO) -> None:
    visible = [r for r in rows if r.category not in _SUMMARY_OMIT_CASH_FX]

    def _is_rshb_up(r: RateRow) -> bool:
        return (r.label or "").startswith("РСХБ UP")

    unionpay_forex_rows = [r for r in visible if r.is_baseline]
    unionpay_up_rows = [r for r in visible if _is_rshb_up(r)]
    unionpay_all_ids = {id(r) for r in (*unionpay_forex_rows, *unionpay_up_rows)}
    remaining = [r for r in visible if id(r) not in unionpay_all_ids]

    print("Базовый курс валюты Forex", file=file)
    for r in unionpay_forex_rows:
        print(r.format_line(baseline), file=file)
    print(file=file)

    print("Карты UnionPay РСХБ", file=file)
    for r in sorted(unionpay_up_rows, key=lambda x: x.rate):
        print(r.format_line(baseline), file=file)

    print(file=file)
    print("Перевод RUB ➔ THB", file=file)
    prev_cat: Optional[SourceCategory] = None
    for r in remaining:
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
    print(
        "  (сводка) Опции: --refresh | --readonly, --json, --filter NAME — пресет постфильтрации строк "
        "(неизвестное имя игнорируется). --readonly — без HTTP, только кеш."
    )
    print(
        "  --gpt PROMPT     Chat API: OPENAI_API_KEY, OPENAI_API_URL; OPENAI_PROMPT; OPENAI_MODEL; OPENAI_HTTP_TIMEOUT_SEC."
    )
    print("  sources              Список id доступных источников.")
    print("  env-status           Файл .env и типичные переменные (без значений).")
    print("  save <файл>          Записать текстовую сводку в файл (те же опции, что и для сводки).")
    print("  usdt [--refresh] [--json] [--cache-file ПУТЬ]  Отчёт P2P RUB/USDT и USDT/THB (отдельный кеш).")
    print(
        "  rshb [THB …] [ATM_FEE]  Отчёт THB/RUB РСХБ UnionPay; 3+ числа — несколько снятий, последнее — комиссия ATM."
    )
    print(
        "  cash [N] [banki|vbr|rbc|all] [K] [--top K] [--sources SPEC] [--fiat USD|EUR|CNY] [--no-banki] [--no-vbr] [--refresh]  "
        "Без N — список городов; с N — курсы города (K или --top — число строк)."
    )
    print(
        "  exchange [--top N] [--lang ru] [--fiat USD|EUR|CNY]   Топ филиалов TT (USD/EUR/CNY→THB)."
    )
    print(
        "  calc RUB usd|eur|cny КУРС [--atm-fee THB]  Сравнение RUB→THB; КУРС — ₽ за 1 ед. валюты (TT)."
    )
    print("  <source_id> summary [--refresh]  Только этот источник (те же --korona-*, --avosend-rub, …).")
    print("  <source_id> --refresh          То же, если других аргументов у id нет.")
    print("  <source_id> [args]   Иные подкоманды источника (см. python ... <id> --help).")
    print(
        "\nПараллельные запросы: переменная RATES_PARALLEL_MAX_WORKERS (сводка источников, "
        "cash, usdt, exchange; по умолчанию см. rates_parallel)."
    )
    print("\nИсточники (кратко; полное: <id> --help):")
    for sid in registered_source_ids():
        mod = plugin_by_id(sid)
        if mod is None:
            continue
        ht = mod.help_text().strip().replace("\n", " ")
        print(f"  {sid}")
        print(f"      {ht}")


def parse_rshb_cli_args(argv: List[str]) -> Tuple[List[float], float]:
    """
    Парсинг хвоста команды `rshb`.

    0 аргументов — (30000,), 250; 1 — (THB,), 250; 2 — (THB,), ATM_FEE;
    3 и более — снимаемые суммы подряд, последнее число — ATM_FEE.
    """
    if not argv:
        return [30_000.0], 250.0
    nums: List[float] = []
    for a in argv:
        try:
            nums.append(float(a))
        except ValueError:
            raise ValueError(f"Неизвестные аргументы rshb: {' '.join(argv)}")
    n = len(nums)
    if n == 1:
        if nums[0] <= 0:
            raise ValueError("THB должен быть больше 0.")
        return [nums[0]], 250.0
    if n == 2:
        if nums[0] <= 0:
            raise ValueError("THB должен быть больше 0.")
        if nums[1] <= 0:
            raise ValueError("ATM_FEE должен быть больше 0.")
        return [nums[0]], nums[1]
    amounts, fee = nums[:-1], nums[-1]
    for x in amounts:
        if x <= 0:
            raise ValueError("THB должен быть больше 0.")
    if fee <= 0:
        raise ValueError("ATM_FEE должен быть больше 0.")
    return amounts, fee


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_arg_parser(add_help=False)
    args, rest = parser.parse_known_args(argv)
    source_ids = frozenset(registered_source_ids())

    if args.gpt_prompt is not None:
        if getattr(args, "readonly", False):
            print("С --readonly нельзя использовать --gpt.", file=sys.stderr)
            return 2
        if rest:
            print(
                "С --gpt не используйте подкоманду: осталось "
                + " ".join(rest),
                file=sys.stderr,
            )
            return 2
        import openai_gpt

        return openai_gpt.run_openai_gpt(args.gpt_prompt)

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
            print("Команда cash-thb удалена. Используйте: cash", file=sys.stderr)
            return 2
        if len(rest) >= 1 and rest[0] == "exchange":
            import exchange_report as _er

            print(_er.exchange_subcommand_help())
            return 0
        if len(rest) >= 1 and rest[0] == "usdt":
            import usdt_report as _ur

            print(_ur.usdt_subcommand_help())
            return 0
        if len(rest) >= 1 and rest[0] == "rshb":
            print(
                "rshb [THB …] [ATM_FEE] — отчёт THB/RUB для РСХБ UnionPay.\n"
                "Два числа: сумма снятия и комиссия ATM; три и больше: несколько сумм, последнее — комиссия.\n"
                "Примеры:\n"
                "  rates.py rshb\n"
                "  rates.py rshb 30000 250\n"
                "  rates.py rshb 30000 20000 10000 250"
            )
            return 0
        if len(rest) >= 1 and rest[0] == "calc":
            import calc_report as _cr

            print(_cr.calc_subcommand_help())
            return 0
        print_global_help(build_arg_parser(add_help=True))
        return 0

    if not rest:
        rows, baseline, warnings = compute_summary_rows(args)
        rows = _maybe_apply_output_filter(args, rows)
        if getattr(args, "readonly", False) and not rows:
            print(
                "--readonly: нет данных сводки в кеше.",
                file=sys.stderr,
            )
            return 1
        if args.json:
            print_json_summary(rows, baseline, warnings, sys.stdout)
        else:
            out_warnings = [] if getattr(args, "readonly", False) else warnings
            print_summary_text(rows, baseline, out_warnings, sys.stdout)
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
        rows = _maybe_apply_output_filter(args, rows)
        if getattr(args, "readonly", False) and not rows:
            print(
                "--readonly: нет данных сводки в кеше.",
                file=sys.stderr,
            )
            return 1
        try:
            with out_path.open("w", encoding="utf-8") as f:
                if args.json:
                    print_json_summary(rows, baseline, warnings, f)
                else:
                    out_warnings = [] if getattr(args, "readonly", False) else warnings
                    print_summary_text(rows, baseline, out_warnings, f)
        except OSError as e:
            print(f"Не удалось записать файл: {e}", file=sys.stderr)
            return 1
        return 0

    if head == "cash":
        import cash_report as cr

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(cr.cash_subcommand_help())
            return 0
        tail = list(rest[1:])
        if args.refresh:
            tail.append("--refresh")
        if getattr(args, "readonly", False):
            tail.append("--readonly")
        err = cr.main_cash_cli(tail)
        return err

    if head == "exchange":
        import exchange_report as er

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(er.exchange_subcommand_help())
            return 0
        # --refresh задан глобально (python rates.py exchange --refresh) парсером
        # выше и не попадаёт в rest — пробрасываем в подкоманду.
        tail = list(rest[1:])
        if args.refresh:
            tail.append("--refresh")
        if getattr(args, "readonly", False):
            tail.append("--readonly")
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
        ro = bool(getattr(args, "readonly", False))
        refresh = False if ro else (u_args.refresh or args.refresh)
        as_json = u_args.json or args.json
        data, uw = ur.compute_usdt_report(
            refresh=refresh,
            cache_file=u_args.cache_file,
            unified_allow_stale=ro,
            readonly=ro,
        )
        if as_json:
            ur.print_usdt_report_json(data, uw, sys.stdout)
        else:
            text = ur.format_usdt_report_text(
                data,
                [] if ro else uw,
            )
            print(text, end="")
        return 0

    if head == "rshb":
        from sources.rshb_unionpay.card_fx_calculator import build_rshb_text

        try:
            thb_nets, atm_fee = parse_rshb_cli_args(rest[1:])
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        try:
            print(
                build_rshb_text(
                    thb_nets=thb_nets,
                    atm_fee_thb=atm_fee,
                    readonly=bool(getattr(args, "readonly", False)),
                ),
                end="",
            )
        except RuntimeError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    if head == "calc":
        import calc_report as cr

        if any(x in ("-h", "--help") for x in rest[1:]):
            print(cr.calc_subcommand_help())
            return 0
        tail = list(rest[1:])
        if args.refresh:
            tail.append("--refresh")
        if getattr(args, "readonly", False):
            tail.append("--readonly")
        return cr.main_calc_cli(tail)

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
            if getattr(args, "readonly", False):
                print(
                    "--readonly: отчёт одного источника всегда ходит в сеть; "
                    "используйте общую сводку без подкоманды.",
                    file=sys.stderr,
                )
                return 2
            return print_single_source_summary(mod, args)
        return mod.command(tail)

    if head in _RESERVED:
        print(f"Зарезервированная команда {head!r} уже обработана — внутренняя ошибка.", file=sys.stderr)
        return 2

    print(f"Неизвестная команда или источник: {head!r}", file=sys.stderr)
    print(
        "Подсказка: --help, sources, save <файл>, usdt, cash, exchange или id источника.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
