#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единое API источников курса **RUB за 1 THB** для сводки.

Каждый источник — :class:`RateSource` с функцией ``fetch(ctx)``, возвращающей
список :class:`SourceQuote` (курс + метка ``label``, опционально ``note``, ``category``, ``emoji``)
или ``None`` / пустой список, если данных нет.

Первый зарегистрированный источник с ``is_baseline=True`` (Forex) задаёт базу для %%;
остальные строки считаются относительно этой базы при выводе.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import rates_unified_cache as uc

from rates_categories import SourceCategory
from rates_parallel import default_max_workers, map_bounded
from rates_primitives import (
    PRIMITIVE_KEYS_BY_SOURCE_ID,
    primitive_keys_for_sources,
    run_ensure_primitives,
)

logger = logging.getLogger(__name__)

# --- Публичные типы --- (SourceCategory — в :mod:`rates_categories`, без цикла с плагинами)


# Порядок блоков «наличные» в сводке (подкатегории не смешиваются сортировкой по курсу).
CASH_CATEGORIES_ORDER: Tuple[SourceCategory, ...] = (
    SourceCategory.CASH_RUB,
    SourceCategory.CASH_USD,
    SourceCategory.CASH_EUR,
    SourceCategory.CASH_CNY,
)

_CASH_SET = frozenset(CASH_CATEGORIES_ORDER)

# Источники, чьи данные приходят из prim без сетевого fetch в ensure_primitives;
# refetched_prims не отражает обновление cron → не используем L1-hit по rs:*.
_SOURCES_SKIP_L1_CACHE_HIT = frozenset({"sberbank_qr"})

# THB за единицу: выше курс — выгоднее клиенту; показываем от большего к меньшему.
_CASH_THB_PER_UNIT_DESC = frozenset(
    {
        SourceCategory.CASH_USD,
        SourceCategory.CASH_EUR,
        SourceCategory.CASH_CNY,
    }
)


def _dedup_should_replace_row(row: RateRow, existing: RateRow) -> bool:
    """
    При коллизии ключа dedup оставляем «лучший» курс для клиента:
    - TRANSFER / CASH_RUB: ниже RUB за 1 THB — лучше;
    - CASH_USD/EUR/CNY: выше THB за 1 единицу валюты — лучше.
    """
    if row.category in _CASH_THB_PER_UNIT_DESC:
        return row.rate > existing.rate
    return row.rate < existing.rate


def is_cash_category(cat: SourceCategory) -> bool:
    return cat in _CASH_SET


@dataclass(frozen=True)
class SourceQuote:
    """Результат одного «курса» от источника: число и подпись."""

    rate: float
    label: str
    note: str = ""
    category: Optional[SourceCategory] = None
    emoji: Optional[str] = None
    #: Если False — в сводке без %% к Forex (др. шкала, напр. THB за 1 USD в наличных).
    compare_to_baseline: bool = True
    #: Порядок внутри ``CASH_RUB``: 0 — обычные строки (сорт. по rate); иначе фикс. блок (РБК×TT).
    cash_rub_seq: int = 0
    #: Одинаковый ключ у пары bitkub + binanceth для строки одного P2P-сценария (слияние при равном rate).
    merge_key: Optional[str] = None


@dataclass
class FetchContext:
    """Параметры запросов (CLI / вызов из кода)."""

    thb_ref: float
    atm_fee: float
    korona_small_rub: float
    korona_large_thb: float
    avosend_rub: float
    unionpay_date: Optional[str]
    moex_override: Optional[float]
    receiving_thb: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    #: Ссылка на unified doc (только :func:`run_sources_unified`) — примитивы ``prim:*``.
    unified_doc: Optional[Dict[str, Any]] = None


SourceFetch = Callable[[FetchContext], Optional[List[SourceQuote]]]


@dataclass(frozen=True)
class RateSource:
    """
    Подключаемый источник.

    ``fetch`` возвращает список котировок (несколько строк — как Avosend).
    ``is_baseline`` только у Forex: первая строка этой группы идёт базой для процентов.
    """

    id: str
    emoji: str
    is_baseline: bool
    category: SourceCategory
    fetch: SourceFetch


def _rate_source_l1_ttl_sec(source_id: str) -> int:
    """
    Персональный TTL L1 для отдельных источников summary.
    Bybit-потоки обновляем чаще, остальные — общий TTL.
    """
    sid = (source_id or "").strip().lower()
    if sid.startswith("bybit"):
        return uc.TTL_L1_RATE_SOURCE_BYBIT_SEC
    return uc.TTL_L1_RATE_SOURCE_SEC


@dataclass
class RateRow:
    """Строка итоговой таблицы (как раньше в rates_summary)."""

    rate: float
    label: str
    emoji: str
    note: str = ""
    is_baseline: bool = False
    category: SourceCategory = SourceCategory.TRANSFER
    compare_to_baseline: bool = True
    cash_rub_seq: int = 0
    merge_key: Optional[str] = None

    def format_line(self, baseline: float) -> str:
        r = f"{self.emoji} {self.rate:.3f}"
        if self.is_baseline:
            tail = f" | {self.label}"
            if self.note:
                tail += f" ({self.note})"
            return r + tail
        if not self.compare_to_baseline:
            tail = f" | {self.label}"
            if self.note:
                tail += f" ({self.note})"
            return r + tail
        pct = (self.rate / baseline - 1.0) * 100.0 if baseline > 0 else 0.0
        tail = f" | {pct:+.1f}% | {self.label}"
        if self.note:
            tail += f" ({self.note})"
        return r + tail


def _cash_sort_key(row: RateRow) -> Tuple[int, int, float, float]:
    """
    Наличные: блоки по :data:`CASH_CATEGORIES_ORDER` (RUB → USD → EUR → CNY).

    * ``CASH_RUB`` (RUB за 1 THB) — все строки по возрастанию ``rate``; при равном курсе —
      по ``cash_rub_seq`` (пары РБК×TT стабильно между собой).
    * ``CASH_USD`` / ``CASH_EUR`` / ``CASH_CNY`` (THB за 1 единицу) — по убыванию ``rate``.
    """
    if row.category in CASH_CATEGORIES_ORDER:
        cat_i = CASH_CATEGORIES_ORDER.index(row.category)
        if row.category == SourceCategory.CASH_RUB:
            return (0, cat_i, row.rate, float(row.cash_rub_seq))
        if row.category in _CASH_THB_PER_UNIT_DESC:
            return (0, cat_i, 0.0, -row.rate)
        return (0, cat_i, 0.0, row.rate)
    return (1, 0, 0.0, row.rate)


def fmt_money_ru(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def quote_to_dict(q: SourceQuote) -> Dict[str, Any]:
    return {
        "rate": q.rate,
        "label": q.label,
        "note": q.note,
        "category": q.category.name if q.category else None,
        "emoji": q.emoji,
        "compare_to_baseline": q.compare_to_baseline,
        "cash_rub_seq": q.cash_rub_seq,
        "merge_key": q.merge_key,
    }


def quote_from_dict(d: Dict[str, Any]) -> SourceQuote:
    cat: Optional[SourceCategory] = None
    c = d.get("category")
    if isinstance(c, str):
        try:
            cat = SourceCategory[c]
        except KeyError:
            cat = None
    return SourceQuote(
        rate=float(d["rate"]),
        label=str(d.get("label", "")),
        note=str(d.get("note", "")),
        category=cat,
        emoji=d.get("emoji"),
        compare_to_baseline=bool(d.get("compare_to_baseline", True)),
        cash_rub_seq=int(d.get("cash_rub_seq", 0)),
        merge_key=d.get("merge_key"),
    )


def _quotes_payload(quotes: Optional[List[SourceQuote]]) -> List[Dict[str, Any]]:
    if not quotes:
        return []
    return [quote_to_dict(q) for q in quotes]


def _quotes_from_payload(data: Any) -> Optional[List[SourceQuote]]:
    if not isinstance(data, list):
        return None
    return [quote_from_dict(x) for x in data if isinstance(x, dict)]


# Склейка *_bitkub + *_binanceth при совпадении итогового rate (см. :attr:`SourceQuote.merge_key`).
MERGE_TH_PAIR_LABELS: Dict[str, str] = {
    "bybit_cash": "Bybit P2P (наличные) → Bitkub / Binance TH",
    "bybit_transfer": "Bybit P2P (перевод) → Bitkub / Binance TH",
    "htx_cash": "HTX P2P (наличные) → Bitkub / Binance TH",
    "htx_no_cash": "HTX P2P (перевод) → Bitkub / Binance TH",
}

MERGE_TH_RATE_EPS: float = 1e-5


def _row_without_merge_key(r: RateRow) -> RateRow:
    return RateRow(
        rate=r.rate,
        label=r.label,
        emoji=r.emoji,
        note=r.note,
        is_baseline=r.is_baseline,
        category=r.category,
        compare_to_baseline=r.compare_to_baseline,
        cash_rub_seq=r.cash_rub_seq,
        merge_key=None,
    )


def _merge_matching_bitkub_binanceth_rows(rows: List[RateRow]) -> List[RateRow]:
    """Две строки с одним ``merge_key`` и (почти) одинаковым ``rate`` → одна с объединённой подписью.

    Порядок строк как в исходном списке (первым остаётся место пары с Bitkub в типичном ``PLUGIN_ORDER``).
    """
    n = len(rows)
    skip = [False] * n
    out: List[RateRow] = []
    for i, r in enumerate(rows):
        if skip[i]:
            continue
        mk = r.merge_key
        if not mk:
            out.append(_row_without_merge_key(r))
            continue
        partner_j: Optional[int] = None
        for j in range(n):
            if j == i or skip[j]:
                continue
            r2 = rows[j]
            if r2.merge_key == mk and abs(r.rate - r2.rate) <= MERGE_TH_RATE_EPS:
                partner_j = j
                break
        if partner_j is not None:
            skip[i] = True
            skip[partner_j] = True
            label = MERGE_TH_PAIR_LABELS.get(mk, f"{r.label} / …")
            out.append(
                RateRow(
                    rate=r.rate,
                    label=label,
                    emoji=r.emoji,
                    note=r.note,
                    is_baseline=r.is_baseline,
                    category=r.category,
                    compare_to_baseline=r.compare_to_baseline,
                    cash_rub_seq=r.cash_rub_seq,
                    merge_key=None,
                )
            )
        else:
            out.append(_row_without_merge_key(r))
    return out


@dataclass
class _SourceFetchPack:
    """Результат одного ``fetch`` в параллельном :func:`run_sources` (отдельный ``warnings``)."""

    quotes: Optional[List[SourceQuote]]
    err: Optional[Exception]
    local_warnings: List[str] = field(default_factory=list)


def _warn_source(src: RateSource, err: Exception, bucket: List[str]) -> None:
    if src.id == "forex":
        bucket.append(f"Forex (Xe): {err}")
    elif src.id == "rshb_unionpay":
        bucket.append(f"РСХБ/UnionPay/MOEX: {err}")
    elif src.id == "bybit_bitkub":
        bucket.append(f"Bybit/Bitkub: {err}")
    elif src.id == "bybit_novawallet":
        bucket.append(f"Bybit/NovaWallet: {err}")
    elif src.id == "bybit_moreta":
        bucket.append(f"Bybit/Moreta: {err}")
    elif src.id == "ex24":
        bucket.append(f"ex24: {err}")
    elif src.id == "kwikpay":
        bucket.append(f"KwikPay: {err}")
    elif src.id == "askmoney":
        bucket.append(f"askmoney: {err}")
    elif src.id == "payscan":
        bucket.append(f"Payscan: {err}")
    elif src.id == "ttexchange":
        bucket.append(f"ttexchange: {err}")
    elif src.id == "tbank":
        bucket.append(f"tbank: {err}")
    elif src.id == "rbc_ttexchange":
        bucket.append(f"rbc_ttexchange: {err}")
    else:
        bucket.append(f"{src.id}: {err}")


def run_sources(
    ctx: FetchContext,
    sources: Optional[Sequence[RateSource]] = None,
    parallel_max_workers: Optional[int] = None,
) -> Tuple[List[RateRow], float, List[str]]:
    """
    Вызывает источники параллельно (пул потоков), склеивает строки в порядке ``sources``.

    У каждого вызова ``fetch`` свой список предупреждений (копия ``ctx``); итоговый
    порядок блоков совпадает с последовательным вариантом.

    Предупреждения: исключения источников и строки, добавленные внутри ``fetch`` в копию контекста.
    """
    seq = list(sources) if sources is not None else list(DEFAULT_SOURCES)
    if not seq or not seq[0].is_baseline:
        raise ValueError("Первый источник должен быть Forex (is_baseline=True)")
    w = ctx.warnings
    rows: List[RateRow] = []

    def worker(src: RateSource) -> _SourceFetchPack:
        lc = replace(ctx, warnings=[])
        try:
            q = src.fetch(lc)
            return _SourceFetchPack(q, None, list(lc.warnings))
        except Exception as e:
            return _SourceFetchPack(None, e, list(lc.warnings))

    for src, pack, thr_exc in map_bounded(
        seq, worker, max_workers=parallel_max_workers
    ):
        if thr_exc is not None:
            raise thr_exc
        assert pack is not None
        w.extend(pack.local_warnings)
        if pack.err:
            _warn_source(src, pack.err, w)
            continue
        quotes = pack.quotes
        if not quotes:
            continue
        for q in quotes:
            cat = q.category if q.category is not None else src.category
            em = q.emoji if q.emoji is not None else src.emoji
            is_bl = src.is_baseline and cat == SourceCategory.TRANSFER
            rows.append(
                RateRow(
                    rate=q.rate,
                    label=q.label,
                    emoji=em,
                    note=q.note,
                    is_baseline=is_bl,
                    category=cat,
                    compare_to_baseline=q.compare_to_baseline,
                    cash_rub_seq=q.cash_rub_seq,
                    merge_key=q.merge_key,
                )
            )

    rows = _merge_matching_bitkub_binanceth_rows(rows)

    forex_rate: Optional[float] = None
    for r in rows:
        if r.is_baseline:
            forex_rate = r.rate
            break
    baseline = forex_rate if forex_rate is not None and forex_rate > 0 else 2.5

    dedup: Dict[Tuple[str, str, str, SourceCategory, bool, int], RateRow] = {}
    for row in rows:
        key = (
            row.label,
            row.note,
            row.emoji,
            row.category,
            row.compare_to_baseline,
            row.cash_rub_seq,
        )
        if key not in dedup or _dedup_should_replace_row(row, dedup[key]):
            dedup[key] = row
    rows = list(dedup.values())

    transfer = [r for r in rows if r.category == SourceCategory.TRANSFER]
    cash = [r for r in rows if is_cash_category(r.category)]

    baseline_rows = [r for r in transfer if r.is_baseline]
    transfer_other = sorted(
        [r for r in transfer if not r.is_baseline],
        key=lambda x: x.rate,
    )
    transfer_ordered = baseline_rows + transfer_other

    cash_ordered = sorted(cash, key=_cash_sort_key)

    rows = transfer_ordered + cash_ordered

    return rows, baseline, w


def run_sources_unified(
    ctx: FetchContext,
    doc: Dict[str, Any],
    ctx_digest: str,
    *,
    refresh: bool,
    sources: Optional[Sequence[RateSource]] = None,
    parallel_max_workers: Optional[int] = None,
) -> Tuple[List[RateRow], float, List[str], Dict[str, int]]:
    """
    Как :func:`run_sources`, но с заполнением ``doc``[\"l1\"] для каждого ``rate_source``.
    Возвращает также словарь версий L1 для записи в L2.
    """
    seq = list(sources) if sources is not None else list(DEFAULT_SOURCES)
    if not seq or not seq[0].is_baseline:
        raise ValueError("Первый источник должен быть Forex (is_baseline=True)")
    w = ctx.warnings
    rows: List[RateRow] = []

    prim_keys = primitive_keys_for_sources([s.id for s in seq])
    refetched_prims = run_ensure_primitives(
        doc,
        prim_keys,
        refresh=refresh,
        max_concurrent=parallel_max_workers,
    )
    logger.info("summary: примитивы готовы, параллельно %d источников сводки", len(seq))
    _pool_cap = default_max_workers() if parallel_max_workers is None else max(1, parallel_max_workers)
    _workers = min(_pool_cap, len(seq))
    logger.info(
        "summary: постановка в пул %d источников (одновременно до %d воркеров)",
        len(seq),
        _workers,
    )
    for i, src in enumerate(seq, start=1):
        logger.info("summary source queued %d/%d %s", i, len(seq), src.id)

    def worker(src: RateSource) -> _SourceFetchPack:
        l1_key = f"rs:{src.id}:{ctx_digest}"
        if not refresh and src.id not in _SOURCES_SKIP_L1_CACHE_HIT:
            hit = uc.l1_get_valid(doc, l1_key)
            if hit is not None:
                prim_for_src = PRIMITIVE_KEYS_BY_SOURCE_ID.get(src.id, ())
                if refetched_prims.isdisjoint(prim_for_src):
                    _ver, payload = hit
                    quotes = _quotes_from_payload(payload)
                    logger.debug("summary source %s: попадание в L1", src.id)
                    return _SourceFetchPack(quotes, None, [])
                logger.debug(
                    "summary source %s: L1 есть, но примитивы обновлены — повторный fetch",
                    src.id,
                )
        logger.debug("summary source worker begin %s", src.id)
        t0 = time.perf_counter()
        lc = replace(ctx, warnings=[], unified_doc=doc)
        try:
            q = src.fetch(lc)
        except Exception as e:
            logger.info(
                "summary source fetch error %s after %.2fs",
                src.id,
                time.perf_counter() - t0,
            )
            return _SourceFetchPack(None, e, list(lc.warnings))
        uc.l1_set(
            doc,
            l1_key,
            _quotes_payload(q),
            ttl_sec=_rate_source_l1_ttl_sec(src.id),
        )
        logger.info(
            "summary source fetch done %s in %.2fs",
            src.id,
            time.perf_counter() - t0,
        )
        return _SourceFetchPack(q, None, list(lc.warnings))

    outcomes = map_bounded(seq, worker, max_workers=parallel_max_workers)
    logger.info("summary: пул источников завершён (%d задач), склейка строк", len(seq))

    for src, pack, thr_exc in outcomes:
        if thr_exc is not None:
            raise thr_exc
        assert pack is not None
        if pack.err:
            _warn_source(src, pack.err, w)
            w.extend(pack.local_warnings)
            continue
        w.extend(pack.local_warnings)
        quotes = pack.quotes
        if not quotes:
            continue
        for q in quotes:
            cat = q.category if q.category is not None else src.category
            em = q.emoji if q.emoji is not None else src.emoji
            is_bl = src.is_baseline and cat == SourceCategory.TRANSFER
            rows.append(
                RateRow(
                    rate=q.rate,
                    label=q.label,
                    emoji=em,
                    note=q.note,
                    is_baseline=is_bl,
                    category=cat,
                    compare_to_baseline=q.compare_to_baseline,
                    cash_rub_seq=q.cash_rub_seq,
                    merge_key=q.merge_key,
                )
            )

    rows = _merge_matching_bitkub_binanceth_rows(rows)

    forex_rate: Optional[float] = None
    for r in rows:
        if r.is_baseline:
            forex_rate = r.rate
            break
    baseline = forex_rate if forex_rate is not None and forex_rate > 0 else 2.5

    dedup: Dict[Tuple[str, str, str, SourceCategory, bool, int], RateRow] = {}
    for row in rows:
        key = (
            row.label,
            row.note,
            row.emoji,
            row.category,
            row.compare_to_baseline,
            row.cash_rub_seq,
        )
        if key not in dedup or _dedup_should_replace_row(row, dedup[key]):
            dedup[key] = row
    rows = list(dedup.values())

    transfer = [r for r in rows if r.category == SourceCategory.TRANSFER]
    cash = [r for r in rows if is_cash_category(r.category)]

    baseline_rows = [r for r in transfer if r.is_baseline]
    transfer_other = sorted(
        [r for r in transfer if not r.is_baseline],
        key=lambda x: x.rate,
    )
    transfer_ordered = baseline_rows + transfer_other

    cash_ordered = sorted(cash, key=_cash_sort_key)

    rows = transfer_ordered + cash_ordered

    deps: Dict[str, int] = {}
    for src in seq:
        l1_key = f"rs:{src.id}:{ctx_digest}"
        ent = doc.get("l1", {}).get(l1_key)
        if isinstance(ent, dict) and "version" in ent:
            deps[l1_key] = int(ent["version"])
    for pk in prim_keys:
        ent = doc.get("prim", {}).get(pk)
        if isinstance(ent, dict) and "version" in ent:
            deps[pk] = int(ent["version"])

    return rows, baseline, w, deps


def collect_rows(
    *,
    thb_ref: float,
    atm_fee: float,
    korona_small_rub: float,
    korona_large_thb: float,
    avosend_rub: float,
    unionpay_date: Optional[str],
    moex_override: Optional[float],
    receiving_thb: Optional[float] = None,
    sources: Optional[Sequence[RateSource]] = None,
    parallel_max_workers: Optional[int] = None,
) -> Tuple[List[RateRow], float, List[str]]:
    """Совместимость с прежним вызовом из rates.py."""
    ctx = FetchContext(
        thb_ref=thb_ref,
        atm_fee=atm_fee,
        korona_small_rub=korona_small_rub,
        korona_large_thb=korona_large_thb,
        avosend_rub=avosend_rub,
        unionpay_date=unionpay_date,
        moex_override=moex_override,
        receiving_thb=receiving_thb,
        warnings=[],
    )
    return run_sources(ctx, sources, parallel_max_workers=parallel_max_workers)


def build_registry(*extra: RateSource) -> List[RateSource]:
    """
    Реестр для :func:`collect_rows`: ``DEFAULT_SOURCES`` + дополнительные источники в конец.

    Forex должен оставаться первым; не вставляйте второй источник с ``is_baseline=True``.
    """
    out = list(DEFAULT_SOURCES)
    out.extend(extra)
    return out


def _load_default_sources() -> Tuple[RateSource, ...]:
    """Отложенный импорт :mod:`sources`, чтобы разорвать цикл импортов плагинов ↔ типы."""
    from sources import load_default_sources

    return load_default_sources()


DEFAULT_SOURCES: Tuple[RateSource, ...] = _load_default_sources()
