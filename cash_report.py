#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Текстовые отчёты «наличные»: курс продажи (``cash``) и цепочка ➔ THB (``cash-thb``).
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rates_parallel import map_bounded
from sources.cash_aggregate import (
    rbc_cash_enabled,
    unified_top_sell_offers,
    vbr_cash_enabled,
)
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
    ("Иркутск", "irkutsk", None),
    ("Екатеринбург", "ekaterinburg", None),
)

_FIAT: Tuple[Tuple[str, int], ...] = (
    ("USD", 3),
    ("EUR", 2),
    ("CNY", 423),
)

_KNOWN_CASH_FIAT = frozenset({"USD", "EUR", "CNY"})

_KNOWN_CASH_SOURCE_TOKENS = frozenset({"all", "banki", "vbr", "rbc"})


def normalize_cash_fiat(s: Optional[str]) -> Optional[str]:
    """USD | EUR | CNY или None (все валюты)."""
    if s is None or str(s).strip() == "":
        return None
    u = str(s).strip().upper()
    if u not in _KNOWN_CASH_FIAT:
        raise ValueError(f"неизвестная валюта для cash: {s!r} (ожидается USD, EUR или CNY)")
    return u


def parse_cash_sources_str(spec: str) -> Tuple[bool, bool, bool]:
    """
    ``all`` или список через запятую: ``rbc``, ``banki``, ``vbr``
    → флаги (use_rbc, use_banki, use_vbr).
    """
    s = spec.strip().lower().replace(" ", "")
    if not s or s == "all":
        return True, True, True
    parts = [p for p in s.split(",") if p]
    valid = frozenset({"rbc", "banki", "vbr"})
    for p in parts:
        if p not in valid:
            raise ValueError(f"неизвестный источник наличных: {p}")
    return ("rbc" in parts, "banki" in parts, "vbr" in parts)


def resolve_cash_sources_flags(
    *,
    sources: Optional[str],
    no_banki: bool,
    no_vbr: bool,
) -> Tuple[bool, bool, bool]:
    """Явный ``--sources`` перекрывает ``--no-banki`` / ``--no-vbr``."""
    if sources:
        return parse_cash_sources_str(sources)
    ur, ub, uv = True, True, True
    if no_banki:
        ub = False
    if no_vbr:
        uv = False
    return ur, ub, uv


def _cash_locations_for_sources(
    use_rbc: bool, use_banki: bool, use_vbr: bool
) -> Tuple[Tuple[str, str, Optional[int]], ...]:
    if use_banki or use_vbr:
        return _CASH_LOCATIONS
    if use_rbc:
        return tuple(loc for loc in _CASH_LOCATIONS if loc[2] is not None)
    return ()


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
    job: _CashCellJob,
    *,
    top_n: int,
    use_rbc: bool = True,
    use_banki: bool,
    use_vbr: bool = True,
    chain_thb: bool = False,
    with_tt_implied: bool = False,
) -> str:
    """L1 ячейки: plain ``cash`` и цепочка ➔THB — разные ключи (разный формат section)."""
    fiat_code, cur_id, _city_label, banki_key, rbc_id = job
    rid = "x" if rbc_id is None else str(int(rbc_id))
    core = (
        f"{fiat_code}:{banki_key}:{rid}:{int(cur_id)}:"
        f"r{1 if use_rbc else 0}:b{1 if use_banki else 0}:ti{1 if with_tt_implied else 0}:vbr{1 if use_vbr else 0}"
        f":tn{int(top_n)}"
    )
    if chain_thb:
        return f"cash_thb:l1:cell:v3:{core}"
    return f"cash:l1:v3:{core}"


def _cash_l2_key(
    *,
    kind: str,
    top_n: int,
    use_rbc: bool = True,
    use_banki: bool,
    use_vbr: bool = True,
    timeout: float,
    fiat: Optional[str] = None,
) -> str:
    ident: Dict[str, Any] = {
        "kind": kind,
        "top_n": int(top_n),
        "use_rbc": bool(use_rbc),
        "use_banki": bool(use_banki),
        "use_vbr": bool(use_vbr),
        "timeout": round(float(timeout), 3),
    }
    if fiat:
        ident["fiat"] = str(fiat)
    # Отдельные L1-ключи ячеек для thb (см. chain_thb); метка сбрасывает старый L2 с телом как у plain cash.
    if kind == "thb":
        ident["cell_l1_ns"] = "cash_thb_cell_v2"
    return f"l2:cash:{ucc.stable_digest(ident)}"


_CASH_FIAT_ORDER: Tuple[str, ...] = ("USD", "EUR", "CNY")
_CASH_SECTION_HEADER_RE = re.compile(r"^(USD|EUR|CNY)\s+")


def _split_cash_report_header(full_text: str) -> Tuple[List[str], List[str]]:
    lines = full_text.splitlines()
    if not lines:
        return [], []
    header: List[str] = [lines[0]]
    i = 1
    if i < len(lines) and not lines[i].strip():
        header.append(lines[i])
        i += 1
    return header, lines[i:]


def _extract_city_sections_from_cash_body(
    body_lines: List[str], city_label: str, top_n: int
) -> Optional[List[str]]:
    out: List[str] = []
    for fiat in _CASH_FIAT_ORDER:
        want = f"{fiat} {city_label}"
        start: Optional[int] = None
        for i, ln in enumerate(body_lines):
            if ln.strip() == want:
                start = i
                break
        if start is None:
            return None
        content: List[str] = []
        j = start + 1
        while j < len(body_lines):
            ln = body_lines[j]
            if not ln.strip():
                break
            if _CASH_SECTION_HEADER_RE.match(ln.strip()):
                break
            content.append(ln)
            j += 1
        if len(content) > top_n:
            content = content[:top_n]
        out.append(body_lines[start])
        out.extend(content)
        out.append("")
    return out


def _extract_city_fiat_section_from_cash_body(
    body_lines: List[str], city_label: str, fiat: str, top_n: int
) -> Optional[List[str]]:
    """Один блок «FIAT Город» (заголовок + до top_n строк курсов)."""
    want = f"{fiat} {city_label}"
    start: Optional[int] = None
    for i, ln in enumerate(body_lines):
        if ln.strip() == want:
            start = i
            break
    if start is None:
        return None
    content: List[str] = []
    j = start + 1
    while j < len(body_lines):
        ln = body_lines[j]
        if not ln.strip():
            break
        if _CASH_SECTION_HEADER_RE.match(ln.strip()):
            break
        content.append(ln)
        j += 1
    if len(content) > top_n:
        content = content[:top_n]
    return [body_lines[start], *content, ""]


def _is_plain_cash_l2_ent(ent: Dict[str, Any]) -> bool:
    deps = ent.get("deps") or {}
    if not isinstance(deps, dict) or not deps:
        return True
    for k in deps:
        if str(k).startswith("cash_thb:l1:cell"):
            return False
    return any(str(k).startswith("cash:l1:") for k in deps)


def _find_best_plain_cash_l2_key_for_city(
    doc: Dict[str, Any],
    city_label: str,
    *,
    top_n: int,
    use_rbc: bool,
    use_banki: bool,
    use_vbr: bool,
    fiat: Optional[str] = None,
) -> Optional[str]:
    """
    Запасной L2 «полный отчёт по всем городам» для вырезки одного города.
    Учитывает top_n и набор источников: нельзя брать снимок, собранный с меньшим
    топом или другим mix РБК/Banki/VBR (иначе в боте /cash N vbr 20 останется 3 строки).
    """
    l2 = doc.get("l2") or {}
    if not isinstance(l2, dict):
        return None
    section_hdr = f"{fiat or 'USD'} {city_label}"
    want_top = int(top_n)
    best_k: Optional[str] = None
    best_sv = -1.0
    for k, ent in l2.items():
        sk = str(k)
        if not sk.startswith("l2:cash:") or ":city:" in sk:
            continue
        if not isinstance(ent, dict):
            continue
        if not _is_plain_cash_l2_ent(ent):
            continue
        body = str(ent.get("text") or "")
        if not body.strip():
            continue
        if not any(ln.strip() == section_hdr for ln in body.splitlines()):
            continue
        pl = ent.get("payload") or {}
        if not isinstance(pl, dict):
            pl = {}
        cached_fiat = pl.get("fiat")
        if cached_fiat and fiat and str(cached_fiat) != str(fiat):
            continue
        cached_top = int(pl.get("top_n", 3))
        if cached_top < want_top:
            continue
        if bool(pl.get("use_rbc", True)) != bool(use_rbc):
            continue
        if bool(pl.get("use_banki", True)) != bool(use_banki):
            continue
        if bool(pl.get("use_vbr", True)) != bool(use_vbr):
            continue
        sv = float(ent.get("saved_unix") or 0)
        if sv > best_sv:
            best_sv = sv
            best_k = sk
    return best_k


def _cash_l2_get_fresh_or_stale(
    doc: Dict[str, Any], key: str, *, allow_stale_l2: bool
) -> Tuple[Optional[Dict[str, Any]], bool]:
    from_stale_l2 = False
    ent = ucc.l2_get(
        doc,
        key,
        ttl_sec=ucc.TTL_L2_CASH_SEC,
        require_fresh=False,
        allow_stale=False,
    )
    if ent is None and allow_stale_l2:
        ent = ucc.l2_get(
            doc,
            key,
            ttl_sec=ucc.TTL_L2_CASH_SEC,
            require_fresh=False,
            allow_stale=True,
        )
        if ent is not None:
            from_stale_l2 = True
    return ent, from_stale_l2


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
    use_rbc: bool = True,
    use_banki: bool,
    use_vbr: bool = True,
    userbot_offers: Optional[List[Any]] = None,
    thb_map: Optional[Dict[str, Any]] = None,
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
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
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
    thb_per = (thb_map or {}).get(fiat_code) if thb_map else None
    for o in offers:
        if thb_per is not None and float(thb_per) > 0:
            implied = float(o.sell) / float(thb_per)
            section.append(
                f"{o.sell:.2f} | {implied:.2f} | "
                f"{o.bank_display} ({o.sources_label()})"
            )
        else:
            section.append(
                f"{o.sell:.2f} | — | {o.bank_display} ({o.sources_label()})"
            )
    section.append("")
    return section, wcell


def _fetch_cash_thb_cell(
    job: _CashCellJob,
    *,
    thb_map: dict,
    top_n: int,
    timeout: float,
    use_rbc: bool = True,
    use_banki: bool,
    use_vbr: bool = True,
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
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
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
    top_n: int = 10,
    timeout: float = 22.0,
    use_rbc: bool = True,
    use_banki: bool = True,
    use_vbr: bool = True,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    city_label: Optional[str] = None,
    readonly: bool = False,
    fiat: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """
    Только курсы продажи наличной валюты (РБК + Banki + VBR при включении).
    Порядок: валюты USD→EUR→CNY, города как в ``_CASH_LOCATIONS``.
    Параметр ``fiat`` — только USD / EUR / CNY (остальные ячейки не строятся).
    """
    global _unified_served_stale_l2_plain

    fiat_norm: Optional[str] = normalize_cash_fiat(fiat) if fiat else None

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    l2_key_base = _cash_l2_key(
        kind="plain_tt",
        top_n=top_n,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        timeout=timeout,
        fiat=fiat_norm,
    )
    legacy_l2_base = _cash_l2_key(
        kind="plain_tt",
        top_n=top_n,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        timeout=timeout,
        fiat=None,
    )
    l2_key = (
        l2_key_base
        if not city_label
        else f"{l2_key_base}:city:{ucc.stable_digest(city_label)}"
    )
    from_stale_l2 = False

    locs_all = _cash_locations_for_sources(use_rbc, use_banki, use_vbr)
    if city_label:
        locs = tuple(x for x in locs_all if x[0] == city_label)
    else:
        locs = locs_all
    city_list = [x[0] for x in locs] if locs else []
    need_rebuild_due_to_userbot = _userbot_has_offers_for_doc(doc, cities=city_list)

    # TT Exchange курс (THB за 1 USD/EUR/CNY) нужен, чтобы посчитать итоговый RUB/THB.
    warnings: List[str] = []
    tt_l1_key = "cash_thb:l1:tt"
    thb_map: Dict[str, Any] = {}
    loaded_tt_from_l1 = False
    if not refresh:
        hit_tt = ucc.l1_get_valid(doc, tt_l1_key)
        if hit_tt is not None:
            p = hit_tt[1]
            if isinstance(p, dict):
                thb_map = dict(p.get("thb_map") or {})
                loaded_tt_from_l1 = True
    if refresh or (not loaded_tt_from_l1 and not readonly):
        thb_map_raw, _tt_branch = _tt_thb_branch()
        thb_map = dict(thb_map_raw or {})
        ucc.l1_set(
            doc,
            tt_l1_key,
            {"thb_map": thb_map, "branch": ""},
            ttl_sec=ucc.TTL_L1_CASH_TT_SEC,
        )
    elif readonly and not loaded_tt_from_l1:
        any_tt = ucc.l1_get_any(doc, tt_l1_key)
        if any_tt is not None:
            p = any_tt[1]
            if isinstance(p, dict):
                thb_map = dict(p.get("thb_map") or {})
                if thb_map:
                    loaded_tt_from_l1 = True
    if not thb_map:
        warnings.append(
            "Нет курсов USD/EUR/CNY у TT Exchange — итоговый RUB/THB не посчитать."
        )

    allow_stale_l2 = bool(unified_allow_stale or readonly)
    hit_ck: Optional[str] = None
    deps: Dict[str, Any] = {}
    if not refresh:
        body = ""
        ent: Optional[Dict[str, Any]] = None
        keys_try: List[str] = []
        seen_k: set[str] = set()

        def _add_key(k: str) -> None:
            if k not in seen_k:
                seen_k.add(k)
                keys_try.append(k)

        _add_key(l2_key)
        if city_label and l2_key != l2_key_base:
            _add_key(l2_key_base)
        if fiat_norm:
            if city_label:
                _add_key(f"{legacy_l2_base}:city:{ucc.stable_digest(city_label)}")
            _add_key(legacy_l2_base)

        for ck in keys_try:
            ent, from_stale_l2 = _cash_l2_get_fresh_or_stale(
                doc, ck, allow_stale_l2=allow_stale_l2
            )
            if ent is None or not str(ent.get("text") or "").strip():
                ent = None
                continue
            slice_from_full = bool(
                city_label and ck == l2_key_base and l2_key != l2_key_base
            )
            deps = ent.get("deps") or {}
            body = str(ent.get("text") or "")
            hdr, rest = _split_cash_report_header(body)
            if city_label and fiat_norm:
                sliced = _extract_city_fiat_section_from_cash_body(
                    rest, city_label, fiat_norm, top_n
                )
                if sliced is None:
                    ent = None
                    body = ""
                    continue
                body = "\n".join(hdr + sliced).rstrip() + "\n"
            elif city_label and slice_from_full:
                sliced = _extract_city_sections_from_cash_body(
                    rest, city_label, top_n
                )
                if sliced is None:
                    ent = None
                    body = ""
                    continue
                body = "\n".join(hdr + sliced).rstrip() + "\n"
            hit_ck = ck
            break

        if ent is None and readonly and city_label:
            alt_k = _find_best_plain_cash_l2_key_for_city(
                doc,
                city_label,
                top_n=top_n,
                use_rbc=use_rbc,
                use_banki=use_banki,
                use_vbr=use_vbr,
                fiat=fiat_norm,
            )
            if alt_k is not None:
                ent, from_stale_l2 = _cash_l2_get_fresh_or_stale(
                    doc, alt_k, allow_stale_l2=allow_stale_l2
                )
                if ent is not None and str(ent.get("text") or "").strip():
                    deps = ent.get("deps") or {}
                    body = str(ent.get("text") or "")
                    hdr, rest = _split_cash_report_header(body)
                    if fiat_norm:
                        sliced = _extract_city_fiat_section_from_cash_body(
                            rest, city_label, fiat_norm, top_n
                        )
                    else:
                        sliced = _extract_city_sections_from_cash_body(
                            rest, city_label, top_n
                        )
                    if sliced is None:
                        ent = None
                        body = ""
                    else:
                        body = "\n".join(hdr + sliced).rstrip() + "\n"
                        hit_ck = alt_k
                else:
                    ent = None

        ro_fragment = bool(
            city_label
            and hit_ck is not None
            and ":city:" not in hit_ck
        )

        if ent is not None and body.strip():
            w = list((ent.get("payload") or {}).get("warnings") or [])
            dep_ok = (not deps) or ucc.l2_deps_match(doc, deps)
            if readonly:
                if ro_fragment:
                    w.append(
                        "readonly: фрагмент города из полного L2 cash (cron/бот кешируют без :city:, "
                        "часто с другим --top)."
                    )
                if not dep_ok:
                    w.append(
                        "readonly: L2 наличные — зависимости L1 не совпадают; показан снимок L2."
                    )
                if need_rebuild_due_to_userbot:
                    w.append(
                        "readonly: есть свежие userbot-данные, сеть отключена — показан старый L2."
                    )
                _unified_served_stale_l2_plain = from_stale_l2
                return body, w
            if dep_ok and not need_rebuild_due_to_userbot:
                _unified_served_stale_l2_plain = from_stale_l2
                return body, w
    _unified_served_stale_l2_plain = False
    if readonly:
        return (
            "Наличные (readonly)\n\n"
            "Нет готового отчёта в L2 unified-кеше (или пустой курс TT в L1 при --readonly).\n",
            warnings
            + [
                "--readonly: без сети не собрать отчёт — обновите кеш командой без --readonly."
            ],
        )
    src_bits: List[str] = []
    if use_rbc and rbc_cash_enabled():
        src_bits.append("РБК")
    if use_banki:
        src_bits.append("Banki")
    if use_vbr and vbr_cash_enabled():
        src_bits.append("VBR")
    src_label = " + ".join(src_bits) if src_bits else "—"
    fiat_bit = f", только {fiat_norm}" if fiat_norm else ""
    cash_header = (
        f"Наличные: {src_label} (топ по курсу продажи){fiat_bit}; RUB/THB после TT Exchange"
    )
    lines: List[str] = [
        cash_header,
        "",
    ]

    if not locs:
        return "", [f"Неизвестный город: {city_label}"]
    jobs = _cash_cell_jobs(locs)
    if fiat_norm:
        jobs = [j for j in jobs if j[0] == fiat_norm]

    def _work(job: _CashCellJob) -> Tuple[List[str], List[str]]:
        fiat_code, _cur_id, city_label, _bk, _rid = job
        ub_offers = _userbot_cash_offers_for_cell(
            doc, fiat_code=fiat_code, city_label=city_label
        )
        k = _cash_cell_l1_key(
            job,
            top_n=top_n,
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
            with_tt_implied=True,
        )
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
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
            userbot_offers=ub_offers,
            thb_map=thb_map,
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
        [
            _cash_cell_l1_key(
                j,
                top_n=top_n,
                use_rbc=use_rbc,
                use_banki=use_banki,
                use_vbr=use_vbr,
                with_tt_implied=True,
            )
            for j in jobs
        ]
        + _chatcash_l1_keys(doc)
    )
    deps_map = _deps_for_l1_keys(doc, dep_keys)
    ucc.l2_set(
        doc,
        l2_key,
        ttl_sec=ucc.TTL_L2_CASH_SEC,
        text=full,
        deps=deps_map,
        payload={
            "warnings": warnings,
            "top_n": int(top_n),
            "use_rbc": bool(use_rbc),
            "use_banki": bool(use_banki),
            "use_vbr": bool(use_vbr),
            "fiat": fiat_norm,
        },
    )
    try:
        ucc.save_unified(doc, unified_path)
    except OSError as e:
        warnings.append(f"Не удалось записать unified-кеш: {unified_path} ({e})")

    return full, warnings


def build_cash_thb_report_text(
    *,
    top_n: int = 10,
    timeout: float = 22.0,
    use_rbc: bool = True,
    use_banki: bool = True,
    use_vbr: bool = True,
    parallel_max_workers: Optional[int] = None,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    readonly: bool = False,
) -> Tuple[str, List[str]]:
    """
    Цепочка: продажа валюты у банка (RUB/ед.) × TT → RUB за 1 THB.
    В каждой строке: курс продажи в источнике | подразумеваемый RUB/THB | банк (источник).
    """
    global _unified_served_stale_l2_thb

    unified_path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(unified_path)
    l2_key = _cash_l2_key(
        kind="thb",
        top_n=top_n,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        timeout=timeout,
    )
    from_stale_l2 = False

    allow_stale_thb = bool(unified_allow_stale or readonly)
    if not refresh:
        ent = ucc.l2_get(
            doc,
            l2_key,
            ttl_sec=ucc.TTL_L2_CASH_THB_SEC,
            require_fresh=False,
            allow_stale=False,
        )
        if ent is None and allow_stale_thb:
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
            body = str(ent.get("text") or "")
            if body.strip():
                w = list((ent.get("payload") or {}).get("warnings") or [])
                dep_ok = (not deps) or ucc.l2_deps_match(doc, deps)
                if readonly:
                    if not dep_ok:
                        w.append(
                            "readonly: L2 cash-thb — зависимости L1 не совпадают; показан снимок L2."
                        )
                    _unified_served_stale_l2_thb = from_stale_l2
                    return body, w
                if dep_ok:
                    _unified_served_stale_l2_thb = from_stale_l2
                    return body, w

    _unified_served_stale_l2_thb = False
    if readonly:
        return (
            "Наличные ➔ THB (readonly)\n\nНет L2 в unified-кеше для этих параметров.\n",
            ["--readonly: нет кешированного отчёта cash-thb."],
        )
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

    locs = _cash_locations_for_sources(use_rbc, use_banki, use_vbr)
    jobs = _cash_cell_jobs(locs)

    def _work_thb(job: _CashCellJob) -> Tuple[List[str], List[str]]:
        k = _cash_cell_l1_key(
            job,
            top_n=top_n,
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
            chain_thb=True,
        )
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
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
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
        _cash_cell_l1_key(
            j,
            top_n=top_n,
            use_rbc=use_rbc,
            use_banki=use_banki,
            use_vbr=use_vbr,
            chain_thb=True,
        )
        for j in jobs
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
    top_n: int = 10,
    timeout: float = 22.0,
    use_rbc: bool = True,
    use_banki: bool = True,
    use_vbr: bool = True,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    city_label: Optional[str] = None,
    readonly: bool = False,
    fiat: Optional[str] = None,
) -> str:
    body, w = build_cash_report_text(
        top_n=top_n,
        timeout=timeout,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
        city_label=city_label,
        readonly=readonly,
        fiat=fiat,
    )
    if readonly:
        return body
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def format_cash_thb_report_with_warnings(
    *,
    top_n: int = 10,
    timeout: float = 22.0,
    use_rbc: bool = True,
    use_banki: bool = True,
    use_vbr: bool = True,
    refresh: bool = False,
    unified_allow_stale: bool = False,
    readonly: bool = False,
) -> str:
    body, w = build_cash_thb_report_text(
        top_n=top_n,
        timeout=timeout,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        refresh=refresh,
        unified_allow_stale=unified_allow_stale,
        readonly=readonly,
    )
    if readonly:
        return body
    if not w:
        return body
    extra = "\n".join(f"  • {x}" for x in w)
    return body + "\nПредупреждения:\n" + extra + "\n"


def cash_subcommand_help() -> str:
    return (
        "cash — курсы продажи наличной валюты по выбранному городу.\n"
        "  cash                          вывести нумерованный список городов.\n"
        "  cash N [banki|vbr|rbc|all] [число_top] [--top K] [--sources SPEC] [--fiat USD|EUR|CNY] …\n"
        "  --fiat — только одна валюта (с номером города N).\n"
        "  SPEC: all, banki, vbr, rbc или через запятую (rbc,banki). "
        "Явный источник перекрывает --no-banki / --no-vbr.\n"
        "  Параллельные ячейки: RATES_PARALLEL_MAX_WORKERS."
    )


def cash_thb_subcommand_help() -> str:
    return (
        "cash-thb — те же топы по продажи × курс TT Exchange → RUB за 1 THB.\n"
        "Формат строки: продажа (RUB/ед.) | RUB/THB | банк (источник).\n"
        "  cash-thb [--top N] [--sources SPEC] [--no-banki] [--no-vbr] [--refresh]   как у cash.\n"
        "  Параллелизм: RATES_PARALLEL_MAX_WORKERS (после одного запроса курсов TT)."
    )


def _parse_cash_argv(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("city_n", nargs="?", type=int, help="Номер города из списка")
    p.add_argument("--top", type=int, default=10, help="Число строк по городу")
    p.add_argument(
        "--sources",
        type=str,
        default=None,
        metavar="SPEC",
        help="Источники: all, banki, vbr, rbc или список через запятую",
    )
    p.add_argument(
        "--no-banki",
        action="store_true",
        help="Не запрашивать Banki.ru (только РБК, Москва и СПб)",
    )
    p.add_argument(
        "--no-vbr",
        action="store_true",
        help="Не запрашивать Выберу.ру (vbr.ru)",
    )
    p.add_argument("--refresh", action="store_true", help="Зарезервировано")
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
        help="Показать только выбранную валюту (только вместе с номером города)",
    )
    p.add_argument("-h", "--help", action="store_true")
    return p.parse_args(argv)


def _strip_standalone_cash_source_tokens(argv: List[str]) -> Tuple[List[str], Optional[str]]:
    spec: Optional[str] = None
    out: List[str] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("-"):
            out.extend(argv[i:])
            break
        al = a.lower()
        if al in _KNOWN_CASH_SOURCE_TOKENS:
            spec = al
            i += 1
            continue
        out.append(a)
        i += 1
    return out, spec


def _inject_cash_top_from_adjacent_ints(filtered: List[str]) -> List[str]:
    if (
        len(filtered) >= 2
        and filtered[0].isdigit()
        and filtered[1].isdigit()
        and not filtered[1].startswith("-")
    ):
        return [filtered[0], "--top", filtered[1], *filtered[2:]]
    return filtered


def main_cash_cli(argv: List[str]) -> int:
    stripped, pos_spec = _strip_standalone_cash_source_tokens(list(argv))
    normalized = _inject_cash_top_from_adjacent_ints(stripped)
    args = _parse_cash_argv(normalized)
    if args.help:
        print(cash_subcommand_help())
        return 0
    if args.top < 1:
        print("--top должен быть >= 1", file=sys.stderr)
        return 2
    spec = args.sources or pos_spec
    try:
        use_rbc, use_banki, use_vbr = resolve_cash_sources_flags(
            sources=spec,
            no_banki=args.no_banki,
            no_vbr=args.no_vbr,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    cities = [x[0] for x in _CASH_LOCATIONS]
    if args.city_n is None:
        if getattr(args, "fiat", None):
            print(
                "Параметр --fiat используется только вместе с номером города (см. cash без аргументов).",
                file=sys.stderr,
            )
            return 2
        print("Доступные города:")
        for i, c in enumerate(cities, start=1):
            print(f"{i}. {c}")
        return 0
    idx = int(args.city_n)
    if idx < 1 or idx > len(cities):
        print(f"Номер города должен быть от 1 до {len(cities)}", file=sys.stderr)
        return 2
    city = cities[idx - 1]
    fiat_kw: Optional[str] = None
    if getattr(args, "fiat", None):
        try:
            fiat_kw = normalize_cash_fiat(args.fiat)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
    text = format_cash_report_with_warnings(
        top_n=args.top,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
        refresh=bool(args.refresh),
        city_label=city,
        readonly=bool(getattr(args, "readonly", False)),
        fiat=fiat_kw,
    )
    sys.stdout.write(text)
    return 0


def main_cash_thb_cli(argv: List[str]) -> int:
    stripped, pos_spec = _strip_standalone_cash_source_tokens(list(argv))
    normalized = _inject_cash_top_from_adjacent_ints(stripped)
    args = _parse_cash_argv(normalized)
    if args.help:
        print(cash_thb_subcommand_help())
        return 0
    if args.top < 1:
        print("--top должен быть >= 1", file=sys.stderr)
        return 2
    spec = args.sources or pos_spec
    try:
        use_rbc, use_banki, use_vbr = resolve_cash_sources_flags(
            sources=spec,
            no_banki=args.no_banki,
            no_vbr=args.no_vbr,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if getattr(args, "fiat", None):
        print(
            "Параметр --fiat поддерживается только у команды cash (не cash-thb).",
            file=sys.stderr,
        )
        return 2
    text = format_cash_thb_report_with_warnings(
        top_n=args.top,
        use_rbc=use_rbc,
        use_banki=use_banki,
        use_vbr=use_vbr,
    )
    sys.stdout.write(text)
    return 0
