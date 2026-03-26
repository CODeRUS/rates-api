#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Текстовые отчёты «наличные»: курс продажи (``cash``) и цепочка ➔ THB (``cash-thb``).
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rates_parallel import map_bounded
from sources.cash_aggregate import unified_top_sell_offers
import rates_unified_cache as ucc

_CashCellJob = Tuple[str, int, str, str, Optional[int]]


class _UserbotOffer:
    def __init__(self, sell: float, bank_display: str) -> None:
        self.sell = sell
        self.bank_display = bank_display

    def sources_label(self) -> str:
        return "Telegram"

# После build_*: ответ из L2 с истёкшим TTL (для фонового обновления в боте).
_unified_served_stale_l2_plain: bool = False
_unified_served_stale_l2_thb: bool = False

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


def _cash_cell_jobs(locs: Tuple[Tuple[str, str, Optional[int]], ...]) -> List[_CashCellJob]:
    jobs: List[_CashCellJob] = []
    for fiat_code, cur_id in _FIAT:
        for city_label, banki_key, rbc_id in locs:
            jobs.append((fiat_code, cur_id, city_label, banki_key, rbc_id))
    return jobs


def _cash_cell_l1_key(
    job: _CashCellJob, *, use_banki: bool, chain_thb: bool = False
) -> str:
    """L1 ячейки: plain ``cash`` и цепочка ➔THB — разные ключи (разный формат section)."""
    fiat_code, cur_id, _city_label, banki_key, rbc_id = job
    rid = "x" if rbc_id is None else str(int(rbc_id))
    core = (
        f"{fiat_code}:{banki_key}:{rid}:{int(cur_id)}:"
        f"b{1 if use_banki else 0}"
    )
    if chain_thb:
        return f"cash_thb:l1:cell:v2:{core}"
    return f"cash:l1:{core}"


def _cash_l2_key(*, kind: str, top_n: int, use_banki: bool, timeout: float) -> str:
    ident: Dict[str, Any] = {
        "kind": kind,
        "top_n": int(top_n),
        "use_banki": bool(use_banki),
        "timeout": round(float(timeout), 3),
    }
    # Отдельные L1-ключи ячеек для thb (см. chain_thb); метка сбрасывает старый L2 с телом как у plain cash.
    if kind == "thb":
        ident["cell_l1_ns"] = "cash_thb_cell_v2"
    return f"l2:cash:{ucc.stable_digest(ident)}"


def _deps_for_l1_keys(doc: Dict[str, Any], keys: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    l1 = doc.get("l1") or {}
    for k in keys:
        ent = l1.get(k)
        if isinstance(ent, dict) and int(ent.get("version", 0)) > 0:
            out[k] = int(ent["version"])
    return out


def _chatcash_l1_keys(doc: Dict[str, Any]) -> List[str]:
    l1 = doc.get("l1") or {}
    if not isinstance(l1, dict):
        return []
    return [str(k) for k in l1.keys() if str(k).startswith("chatcash:")]


def _userbot_has_offers_for_doc(
    doc: Dict[str, Any],
    *,
    cities: List[str],
) -> bool:
    """
    Проверить, есть ли у userbot релевантные офферы (USD/EUR/CNY в указанных городах).
    Используется, чтобы не отдавать устаревший L2 (построенный до появления userbot-данных).
    """
    want_cat_by_currency = {
        "USD": "cash_usd",
        "EUR": "cash_eur",
        "CNY": "cash_cny",
    }
    l1 = doc.get("l1") or {}
    if not isinstance(l1, dict):
        return False
    cities_set = set([c for c in cities if c])
    if not cities_set:
        return False

    for k in l1.keys():
        sk = str(k)
        if not sk.startswith("chatcash:"):
            continue
        hit = ucc.l1_get_valid(doc, sk)
        if hit is None:
            continue
        payload = hit[1]
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            city = str(row.get("city") or "").strip()
            if city not in cities_set:
                continue
            cur = str(row.get("currency") or "").upper()
            want_cat = want_cat_by_currency.get(cur)
            if want_cat is None:
                continue
            cat = str(row.get("category") or "").strip().lower()
            if cat != want_cat:
                continue
            try:
                rate = float(row.get("rate") or 0)
            except (TypeError, ValueError):
                continue
            if rate > 0:
                return True
    return False


def _userbot_cash_offers_for_cell(
    doc: Dict[str, Any],
    *,
    fiat_code: str,
    city_label: str,
) -> List[Any]:
    """Офферы из userbot (L1 chatcash:*) для секции cash по городу/валюте."""
    want_cat = {
        "USD": "cash_usd",
        "EUR": "cash_eur",
        "CNY": "cash_cny",
    }.get(fiat_code.upper())
    if want_cat is None:
        return []
    out: List[Any] = []
    l1 = doc.get("l1") or {}
    if not isinstance(l1, dict):
        return out
    for k in l1.keys():
        sk = str(k)
        if not sk.startswith("chatcash:"):
            continue
        hit = ucc.l1_get_valid(doc, sk)
        if hit is None:
            continue
        payload = hit[1]
        if not isinstance(payload, list):
            continue
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("category") or "").strip().lower() != want_cat:
                continue
            if str(row.get("city") or "").strip() != city_label:
                continue
            try:
                rate = float(row.get("rate") or 0)
            except (TypeError, ValueError):
                continue
            if rate <= 0:
                continue
            src = str(row.get("source_name") or row.get("source_id") or "Userbot").strip()
            bank_display = src if src else "Userbot"
            out.append(_UserbotOffer(rate, bank_display))
    return out


def _fetch_cash_cell(
    job: _CashCellJob,
    *,
    top_n: int,
    timeout: float,
    use_banki: bool,
    userbot_offers: Optional[List[Any]] = None,
) -> Tuple[List[str], List[str]]:
    fiat_code, cur_id, city_label, banki_key, rbc_id = job
    section: List[str] = [f"{fiat_code} {city_label}"]
    wcell: List[str] = []
    offers, w = unified_top_sell_offers(
        fiat_code=fiat_code,
        banki_region_key=banki_key,
        rbc_city_id=rbc_id,
        rbc_currency_id=cur_id,
        top_n=top_n,
        timeout=timeout,
        use_banki=use_banki,
    )
    if userbot_offers:
        # userbot-курсы всегда показываем, даже если они "вытесняют" часть топ-офферов.
        # Базовый список `offers` уже ограничен `top_n` в unified_top_sell_offers().
        combined = list(offers)
        seen: set[tuple[float, str]] = set()
        for o in combined:
            seen.add((float(getattr(o, "sell", 0) or 0), str(getattr(o, "bank_display", ""))))
        for u in userbot_offers:
            key = (float(getattr(u, "sell", 0) or 0), str(getattr(u, "bank_display", "")))
            if key in seen:
                continue
            seen.add(key)
            combined.append(u)
        offers = sorted(
            combined, key=lambda x: (float(getattr(x, "sell", 0) or 0), str(getattr(x, "bank_display", "")))
        )
    wcell.extend(w)
    if not offers:
        section.append("(нет котировок sell)")
        section.append("")
        wcell.append(f"Нет sell: {fiat_code} {city_label}")
        return section, wcell
    for o in offers:
        section.append(f"{o.sell:.2f} | {o.bank_display} ({o.sources_label()})")
    section.append("")
    return section, wcell


def _fetch_cash_thb_cell(
    job: _CashCellJob,
    *,
    thb_map: dict,
    top_n: int,
    timeout: float,
    use_banki: bool,
) -> Tuple[List[str], List[str]]:
    fiat_code, cur_id, city_label, banki_key, rbc_id = job
    section: List[str] = [f"{fiat_code} {city_label}"]
    wcell: List[str] = []
    thb_per = thb_map.get(fiat_code)
    offers, w = unified_top_sell_offers(
        fiat_code=fiat_code,
        banki_region_key=banki_key,
        rbc_city_id=rbc_id,
        rbc_currency_id=cur_id,
        top_n=top_n,
        timeout=timeout,
        use_banki=use_banki,
    )
    wcell.extend(w)
    if not offers:
        section.append("(нет котировок sell)")
        section.append("")
        wcell.append(f"Нет sell: {fiat_code} {city_label}")
        return section, wcell
    if thb_per is not None and thb_per > 0:
        for o in offers:
            implied = o.sell / thb_per
            section.append(
                f"{o.sell:.2f} | {implied:.2f} | "
                f"{o.bank_display} ({o.sources_label()})"
            )
        section.append("")
        return section, wcell
    for o in offers:
        section.append(
            f"{o.sell:.2f} | — | {o.bank_display} "
            f"({o.sources_label()}) (нет THB/{fiat_code} у TT)"
        )
    section.append("")
    wcell.append(f"Нет TT {fiat_code}: {city_label}")
    return section, wcell


def build_cash_report_text(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    city_label: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Только курсы продажи наличной валюты (РБК + Banki).
    Порядок: валюты USD→EUR→CNY, города как в ``_CASH_LOCATIONS``.
    """
    global _unified_served_stale_l2_plain

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    l2_key = _cash_l2_key(
        kind="plain", top_n=top_n, use_banki=use_banki, timeout=timeout
    )
    if city_label:
        l2_key = f"{l2_key}:city:{ucc.stable_digest(city_label)}"
    from_stale_l2 = False

    locs_all = _locations(use_banki)
    if city_label:
        locs = tuple(x for x in locs_all if x[0] == city_label)
    else:
        locs = locs_all
    city_list = [x[0] for x in locs] if locs else []
    need_rebuild_due_to_userbot = _userbot_has_offers_for_doc(doc, cities=city_list)

    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_CASH_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and unified_allow_stale:
            ent = ucc.l2_get(
                doc,
                l2_key,
                ttl_sec=ucc.TTL_L2_CASH_SEC,
                require_fresh=False,
                allow_stale=True,
            )
            if ent is not None:
                from_stale_l2 = True
        if ent is not None:
            deps = ent.get("deps") or {}
            if ((not deps) or ucc.l2_deps_match(doc, deps)) and not need_rebuild_due_to_userbot:
                body = str(ent.get("text") or "")
                if body.strip():
                    w = list((ent.get("payload") or {}).get("warnings") or [])
                    _unified_served_stale_l2_plain = from_stale_l2
                    return body, w

    _unified_served_stale_l2_plain = False
    warnings: List[str] = []
    lines: List[str] = [
        "Наличные: РБК + Banki (топ по курсу продажи)",
        "",
    ]

    if not locs:
        return "", [f"Неизвестный город: {city_label}"]
    jobs = _cash_cell_jobs(locs)

    def _work(job: _CashCellJob) -> Tuple[List[str], List[str]]:
        fiat_code, _cur_id, city_label, _bk, _rid = job
        ub_offers = _userbot_cash_offers_for_cell(
            doc, fiat_code=fiat_code, city_label=city_label
        )
        k = _cash_cell_l1_key(job, use_banki=use_banki)
        can_use_l1 = (not ub_offers)
        if (not refresh) and can_use_l1:
            hit = ucc.l1_get_valid(doc, k)
            if hit is not None:
                payload = hit[1]
                if isinstance(payload, dict):
                    return list(payload.get("section") or []), list(
                        payload.get("wcell") or []
                    )
        sec, wcell = _fetch_cash_cell(
            job,
            top_n=top_n,
            timeout=timeout,
            use_banki=use_banki,
            userbot_offers=ub_offers,
        )
        ucc.l1_set(
            doc,
            k,
            {"section": sec, "wcell": wcell},
            ttl_sec=ucc.TTL_L1_CASH_CELL_SEC,
        )
        return sec, wcell

    for _job, pack, exc in map_bounded(
        jobs, _work, max_workers=parallel_max_workers
    ):
        if exc is not None:
            raise exc
        assert pack is not None
        sec, wcell = pack
        warnings.extend(wcell)
        lines.extend(sec)

    full = "\n".join(lines).rstrip() + "\n"
    dep_keys = (
        [_cash_cell_l1_key(j, use_banki=use_banki) for j in jobs]
        + _chatcash_l1_keys(doc)
    )
    deps_map = _deps_for_l1_keys(doc, dep_keys)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_CASH_SEC,
        text=full,
        deps=deps_map,
        payload={"warnings": warnings},
    )
    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    return full, warnings


def build_cash_thb_report_text(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
) -> Tuple[str, List[str]]:
    """
    Цепочка: продажа валюты у банка (RUB/ед.) × TT → RUB за 1 THB.
    В каждой строке: курс продажи в источнике | подразумеваемый RUB/THB | банк (источник).
    """
    global _unified_served_stale_l2_thb

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    l2_key = _cash_l2_key(
        kind="thb", top_n=top_n, use_banki=use_banki, timeout=timeout
    )
    from_stale_l2 = False

    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_CASH_THB_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and unified_allow_stale:
            ent = ucc.l2_get(
                doc,
                l2_key,
                ttl_sec=ucc.TTL_L2_CASH_THB_SEC,
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
                    _unified_served_stale_l2_thb = from_stale_l2
                    return body, w

    _unified_served_stale_l2_thb = False
    warnings: List[str] = []
    tt_l1_key = "cash_thb:l1:tt"
    thb_map: Dict[str, Any] = {}
    tt_branch = ""
    loaded_tt_from_l1 = False
    if not refresh:
        hit_tt = ucc.l1_get_valid(doc, tt_l1_key)
        if hit_tt is not None:
            p = hit_tt[1]
            if isinstance(p, dict):
                thb_map = dict(p.get("thb_map") or {})
                tt_branch = str(p.get("branch") or "")
                loaded_tt_from_l1 = True
    if refresh or not loaded_tt_from_l1:
        thb_map_raw, tt_branch = _tt_thb_branch()
        thb_map = dict(thb_map_raw or {})
        ucc.l1_set(
            doc,
            tt_l1_key,
            {"thb_map": thb_map, "branch": tt_branch},
            ttl_sec=ucc.TTL_L1_CASH_TT_SEC,
        )

    if not thb_map:
        warnings.append(
            "Нет курсов USD/EUR/CNY у TT Exchange — цепочки ➔ THB не посчитать."
        )

    lines: List[str] = [
        "Наличные ➔ THB: продажа (RUB/ед.) | RUB/THB | банк (источник)",
        "",
    ]

    locs = _locations(use_banki)
    jobs = _cash_cell_jobs(locs)

    def _work_thb(job: _CashCellJob) -> Tuple[List[str], List[str]]:
        k = _cash_cell_l1_key(job, use_banki=use_banki, chain_thb=True)
        if not refresh:
            hit = ucc.l1_get_valid(doc, k)
            if hit is not None:
                payload = hit[1]
                if isinstance(payload, dict):
                    return list(payload.get("section") or []), list(
                        payload.get("wcell") or []
                    )
        sec, wcell = _fetch_cash_thb_cell(
            job,
            thb_map=thb_map,
            top_n=top_n,
            timeout=timeout,
            use_banki=use_banki,
        )
        ucc.l1_set(
            doc,
            k,
            {"section": sec, "wcell": wcell},
            ttl_sec=ucc.TTL_L1_CASH_CELL_SEC,
        )
        return sec, wcell

    for _job, pack, exc in map_bounded(
        jobs, _work_thb, max_workers=parallel_max_workers
    ):
        if exc is not None:
            raise exc
        assert pack is not None
        sec, wcell = pack
        warnings.extend(wcell)
        lines.extend(sec)

    full = "\n".join(lines).rstrip() + "\n"
    dep_keys = [tt_l1_key] + [
        _cash_cell_l1_key(j, use_banki=use_banki, chain_thb=True) for j in jobs
    ]
    deps_map = _deps_for_l1_keys(doc, dep_keys)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_CASH_THB_SEC,
        text=full,
        deps=deps_map,
        payload={"warnings": warnings},
    )
    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    return full, warnings


def format_cash_report_with_warnings(
    *,
    top_n: int = 3,
    timeout: float = 22.0,
    use_banki: bool = True,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    city_label: Optional[str] = None,
) -> str:
    body, w = build_cash_report_text(
        top_n=top_n,
        timeout=timeout,
        use_banki=use_banki,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
        city_label=city_label,
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
    refresh: bool = False,
    unified_allow_stale: bool = False,
) -> str:
    body, w = build_cash_thb_report_text(
        top_n=top_n,
        timeout=timeout,
        use_banki=use_banki,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
    )
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def cash_subcommand_help() -> str:
    return (
        "cash — курсы продажи наличной валюты по выбранному городу.\n"
        "  cash                          вывести нумерованный список городов.\n"
        "  cash N [--top N] [--no-banki] [--refresh]   вывести только город №N.\n"
        "  Параллельные ячейки валюта×город: RATES_PARALLEL_MAX_WORKERS."
    )


def cash_thb_subcommand_help() -> str:
    return (
        "cash-thb — те же топы по продажи × курс TT Exchange → RUB за 1 THB.\n"
        "Формат строки: продажа (RUB/ед.) | RUB/THB | банк (источник).\n"
        "  cash-thb [--top N] [--no-banki] [--refresh]   как у cash.\n"
        "  Параллелизм: RATES_PARALLEL_MAX_WORKERS (после одного запроса курсов TT)."
    )


def _parse_cash_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("city_n", nargs="?", type=int, help="Номер города из списка")
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
    locs = _locations(not args.no_banki)
    cities = [x[0] for x in locs]
    if args.city_n is None:
        print("Доступные города:")
        for i, c in enumerate(cities, start=1):
            print(f"{i}. {c}")
        return 0
    idx = int(args.city_n)
    if idx < 1 or idx > len(cities):
        print(f"Номер города должен быть от 1 до {len(cities)}", file=sys.stderr)
        return 2
    city = cities[idx - 1]
    text = format_cash_report_with_warnings(
        top_n=args.top,
        use_banki=not args.no_banki,
        city_label=city,
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
