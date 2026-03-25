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

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Dict, List, Optional, Sequence, Tuple


# --- Публичные типы ---


class SourceCategory(Enum):
    """Категория источника: переводы vs наличные в обменнике."""

    TRANSFER = "transfer"
    CASH = "cash"


@dataclass(frozen=True)
class SourceQuote:
    """Результат одного «курса» от источника: число и подпись."""

    rate: float
    label: str
    note: str = ""
    category: Optional[SourceCategory] = None
    emoji: Optional[str] = None


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
    warnings: List[str] = field(default_factory=list)


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


@dataclass
class RateRow:
    """Строка итоговой таблицы (как раньше в rates_summary)."""

    rate: float
    label: str
    emoji: str
    note: str = ""
    is_baseline: bool = False
    category: SourceCategory = SourceCategory.TRANSFER

    def format_line(self, baseline: float) -> str:
        r = f"{self.emoji} {self.rate:.3f}"
        if self.is_baseline:
            tail = f" | {self.label}"
            if self.note:
                tail += f" ({self.note})"
            return r + tail
        pct = (self.rate / baseline - 1.0) * 100.0 if baseline > 0 else 0.0
        tail = f" | {pct:+.1f}% | {self.label}"
        if self.note:
            tail += f" ({self.note})"
        return r + tail


def fmt_money_ru(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


def _warn_source(src: RateSource, err: Exception, bucket: List[str]) -> None:
    if src.id == "forex":
        bucket.append(f"Forex (Xe): {err}")
    elif src.id == "rshb_unionpay":
        bucket.append(f"РСХБ/UnionPay/MOEX: {err}")
    elif src.id == "bybit_bitkub":
        bucket.append(f"Bybit/Bitkub: {err}")
    elif src.id == "ex24":
        bucket.append(f"ex24: {err}")
    elif src.id == "kwikpay":
        bucket.append(f"KwikPay: {err}")
    elif src.id == "askmoney":
        bucket.append(f"askmoney: {err}")
    elif src.id == "ttexchange":
        bucket.append(f"ttexchange: {err}")
    else:
        bucket.append(f"{src.id}: {err}")


def run_sources(
    ctx: FetchContext,
    sources: Optional[Sequence[RateSource]] = None,
) -> Tuple[List[RateRow], float, List[str]]:
    """
    Последовательно вызывает источники. Первый в списке — Forex (``is_baseline=True``).

    Предупреждения: исключения источников и строки, добавленные в ``ctx.warnings`` внутри fetch.
    """
    seq = list(sources) if sources is not None else list(DEFAULT_SOURCES)
    if not seq or not seq[0].is_baseline:
        raise ValueError("Первый источник должен быть Forex (is_baseline=True)")
    w = ctx.warnings
    rows: List[RateRow] = []

    for src in seq:
        try:
            quotes = src.fetch(ctx)
        except Exception as e:
            _warn_source(src, e, w)
            quotes = None

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
                )
            )

    forex_rate: Optional[float] = None
    for r in rows:
        if r.is_baseline:
            forex_rate = r.rate
            break
    baseline = forex_rate if forex_rate is not None and forex_rate > 0 else 2.5

    dedup: Dict[Tuple[str, str, str, SourceCategory], RateRow] = {}
    for row in rows:
        key = (row.label, row.note, row.emoji, row.category)
        if key not in dedup or row.rate < dedup[key].rate:
            dedup[key] = row
    rows = list(dedup.values())

    transfer = [r for r in rows if r.category == SourceCategory.TRANSFER]
    cash = [r for r in rows if r.category == SourceCategory.CASH]

    baseline_rows = [r for r in transfer if r.is_baseline]
    transfer_other = sorted(
        [r for r in transfer if not r.is_baseline],
        key=lambda x: x.rate,
    )
    transfer_ordered = baseline_rows + transfer_other

    cash_ordered = sorted(cash, key=lambda x: x.rate)

    rows = transfer_ordered + cash_ordered

    return rows, baseline, w


def collect_rows(
    *,
    thb_ref: float,
    atm_fee: float,
    korona_small_rub: float,
    korona_large_thb: float,
    avosend_rub: float,
    unionpay_date: Optional[str],
    moex_override: Optional[float],
    sources: Optional[Sequence[RateSource]] = None,
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
        warnings=[],
    )
    return run_sources(ctx, sources)


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
