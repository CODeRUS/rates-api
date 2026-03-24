#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Единое API источников курса **RUB за 1 THB** для сводки.

Каждый источник — :class:`RateSource` с функцией ``fetch(ctx)``, возвращающей
список :class:`SourceQuote` (курс + текстовая метка ``label``, опционально ``note``)
или ``None`` / пустой список, если данных нет.

Первый зарегистрированный источник с ``is_baseline=True`` (Forex) задаёт базу для %%;
остальные строки считаются относительно этой базы при выводе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Dict, List, Optional, Sequence, Tuple

# --- Публичные типы ---


@dataclass(frozen=True)
class SourceQuote:
    """Результат одного «курса» от источника: число и подпись."""

    rate: float
    label: str
    note: str = ""


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
    fetch: SourceFetch


@dataclass
class RateRow:
    """Строка итоговой таблицы (как раньше в rates_summary)."""

    rate: float
    label: str
    emoji: str
    note: str = ""
    is_baseline: bool = False

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


def _fmt_money_ru(n: float) -> str:
    return f"{n:,.0f}".replace(",", " ")


# --- Реализации fetch ---


def fetch_forex(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import forex_xe_api as xe

    conv = xe.midmarket_convert("THB", "RUB", 1.0)
    return [SourceQuote(float(conv["result"]), "Forex")]


def fetch_rshb_unionpay(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import card_fx_calculator as cfx

    on = date.fromisoformat(ctx.unionpay_date) if ctx.unionpay_date else None
    (
        cpt,
        moex,
        sell_dec,
        _,
        sell_online_dec,
        _,
        live_stale,
        _,
    ) = cfx.fetch_live_inputs(on, ctx.moex_override)
    rshb_sell = float(sell_dec)
    rshb_app = float(sell_online_dec)
    broker_cny_rub = float(moex) if moex else 0.0

    out: List[SourceQuote] = []
    if cpt > 0 and broker_cny_rub > 0:
        out.append(
            SourceQuote(cfx.rub_per_thb(cpt, broker_cny_rub), "РСХБ UP CNY (брокер, оплата)")
        )
    if cpt > 0 and rshb_app > 0:
        out.append(
            SourceQuote(cfx.rub_per_thb(cpt, rshb_app), "РСХБ UP CNY (приложение, оплата)")
        )
    if cpt > 0 and rshb_sell > 0:
        out.append(SourceQuote(cfx.rub_per_thb(cpt, rshb_sell), "РСХБ UP RUB (оплата)"))

    thb_ref, atm_fee = ctx.thb_ref, ctx.atm_fee
    if cpt > 0 and broker_cny_rub > 0:
        _rub_atm, rpt = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            broker_cny_rub,
            issuer_fee_on_cny_base=0.03,
        )
        out.append(
            SourceQuote(
                rpt,
                f"РСХБ UP CNY (брокер, снятие {thb_ref:.0f}+{atm_fee:.0f})",
            )
        )
        if rshb_app > 0:
            _rub2, rpt2 = cfx.atm_rub_from_cny_path(
                thb_ref,
                atm_fee,
                cpt,
                rshb_app,
                issuer_fee_on_cny_base=0.03,
            )
            out.append(
                SourceQuote(
                    rpt2,
                    f"РСХБ UP CNY (приложение, снятие {thb_ref:.0f}+{atm_fee:.0f})",
                )
            )
    if cpt > 0 and rshb_sell > 0:
        _rub_rc, rpt_rc = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            rshb_sell,
            issuer_fee_on_cny_base=0.0,
            extra_rub_pct_of_base=0.01,
        )
        out.append(
            SourceQuote(
                rpt_rc,
                f"РСХБ UP RUB (снятие {thb_ref:.0f}+{atm_fee:.0f})",
            )
        )

    if live_stale:
        ctx.warnings.append(
            "РСХБ/UnionPay/MOEX: таймаут сети — в расчётах использованы "
            f"последние сохранённые курсы ({cfx.LIVE_INPUTS_CACHE_FILE.name})."
        )
    return out or None


def fetch_bybit_bitkub(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import bitkub_usdt_thb as bk
    import bybit_p2p_usdt_rub as bp

    items = bp.fetch_all_online_items(size=20, verification_filter=0)
    a = bp.filter_cash_deposit_to_bank(items, 99.0)
    b = bp.filter_bank_transfer_no_cash(items, 99.0)
    ia = bp.min_by_price(a)
    ib = bp.min_by_price(b)
    tk = bk.fetch_ticker()
    thb_usdt = float(tk.get("highestBid") or 0)
    if thb_usdt <= 0:
        ctx.warnings.append("Bitkub: нет highestBid для USDT")
        return None

    out: List[SourceQuote] = []
    if ia:
        out.append(SourceQuote(float(ia["price"]) / thb_usdt, "Bybit P2P (cash) → Bitkub"))
    else:
        ctx.warnings.append("Bybit: нет объявлений Cash Deposit (18) с completion≥99")
    if ib:
        out.append(SourceQuote(float(ib["price"]) / thb_usdt, "Bybit P2P (перевод) → Bitkub"))
    else:
        ctx.warnings.append("Bybit: нет объявлений только перевод (14, без 18) с completion≥99")
    return out or None


def fetch_korona(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import koronapay_tariffs as kp

    out: List[SourceQuote] = []
    large = ctx.korona_large_thb
    lbl_large = f"Korona (от {_fmt_money_ru(large)} THB)"
    try:
        rows_kp = kp.fetch_tariffs(receiving_amount_satang=kp.thb_to_satang(large))
        row = rows_kp[0]
        rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
        thb = kp.satang_to_thb(int(row["receivingAmount"]))
        if thb > 0:
            out.append(SourceQuote(rub / thb, lbl_large))
    except Exception as e:
        ctx.warnings.append(f"Korona {lbl_large}: {e}")

    small = ctx.korona_small_rub
    try:
        rows_kp = kp.fetch_tariffs(sending_amount_kopecks=kp.rub_to_kopecks(small))
        row = rows_kp[0]
        rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
        thb = kp.satang_to_thb(int(row["receivingAmount"]))
        if thb > 0:
            out.append(SourceQuote(rub / thb, "Korona (малые суммы)"))
    except Exception as e:
        ctx.warnings.append(f"Korona (малые суммы): {e}")

    return out or None


def fetch_avosend(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import avosend_commission as av

    amt = ctx.avosend_rub
    note = f"от {_fmt_money_ru(amt)} RUB"
    avo_label = f"Avosend (от {_fmt_money_ru(amt)} RUB)"

    def rate_mode(mode: av.TransferMode) -> Optional[float]:
        try:
            d = av.fetch_commission(amt, mode)
            fr = float(d.get("from"))
            to = float(d.get("to"))
            if to <= 0:
                return None
            return fr / to
        except Exception as e:
            ctx.warnings.append(f"Avosend {mode.value}: {e}")
            return None

    r_bank = rate_mode(av.TransferMode.BANK_ACCOUNT)
    r_cash = rate_mode(av.TransferMode.CASH)
    if r_bank is None and r_cash is None:
        return None
    if r_bank is not None and r_cash is not None:
        if abs(r_bank - r_cash) <= max(1e-9, 1e-9 * abs(r_bank)):
            return [SourceQuote(r_bank, avo_label)]
        return [
            SourceQuote(r_bank, "Avosend на счёт", note=note),
            SourceQuote(r_cash, "Avosend наличные", note=note),
        ]
    if r_bank is not None:
        return [SourceQuote(r_bank, "Avosend на счёт", note=note)]
    return [SourceQuote(r_cash, "Avosend наличные", note=note)]


def fetch_ex24(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import ex24_rub_thb as e24

    rr = e24.try_fetch_real_rate_rub_thb() or e24.DEFAULT_REAL_RATE
    rub_best = float(e24.RUB_MIN_FOR_ZERO_MARKUP)
    r_ex = e24.customer_rate_rub_per_thb(rub_best, rr)
    return [SourceQuote(r_ex, "Ex24.pro", note=f"от {_fmt_money_ru(rub_best)} RUB")]


def fetch_kwikpay(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import kwikpay_rates as kw

    kq = kw.fetch_quotes_for_amounts([30_001])
    if not kq:
        return None
    q = kq[0]
    if q.withdraw_thb <= 0:
        return None
    if q.fee_rub != 0:
        ctx.warnings.append(
            f"KwikPay: при amount=30001 комиссия не 0 ({q.fee_rub:g} RUB), курс всё же выведен"
        )
    return [SourceQuote(q.rub_per_thb, "KwikPay (от 30001 RUB)")]


def fetch_askmoney(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    import askmoney_rub_thb as am

    html = am.fetch_homepage_html()
    params = am.parse_params_from_html(html)
    best_rub, _bthb, _brt = am.min_effective_rate_rub_per_thb(params)
    thb_at = am.rub_to_thb(best_rub, params)
    rt = am.effective_rate_rub_per_thb(best_rub, thb_at)
    if rt is None:
        return None
    return [SourceQuote(rt, "askmoney.pro", note=f"от {_fmt_money_ru(best_rub)} RUB")]


# --- Реестр по умолчанию (Forex строго первым) ---

DEFAULT_SOURCES: Tuple[RateSource, ...] = (
    RateSource("forex", "📈", True, fetch_forex),
    RateSource("rshb_unionpay", "💳", False, fetch_rshb_unionpay),
    RateSource("bybit_bitkub", "💸", False, fetch_bybit_bitkub),
    RateSource("korona", "💱", False, fetch_korona),
    RateSource("avosend", "💱", False, fetch_avosend),
    RateSource("ex24", "🤑", False, fetch_ex24),
    RateSource("kwikpay", "💱", False, fetch_kwikpay),
    RateSource("askmoney", "🤑", False, fetch_askmoney),
)


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
            rows.append(
                RateRow(
                    rate=q.rate,
                    label=q.label,
                    emoji=src.emoji,
                    note=q.note,
                    is_baseline=src.is_baseline,
                )
            )

    forex_rate: Optional[float] = None
    for r in rows:
        if r.is_baseline:
            forex_rate = r.rate
            break
    baseline = forex_rate if forex_rate is not None and forex_rate > 0 else 2.5

    dedup: Dict[Tuple[str, str, str], RateRow] = {}
    for row in rows:
        key = (row.label, row.note, row.emoji)
        if key not in dedup or row.rate < dedup[key].rate:
            dedup[key] = row
    rows = list(dedup.values())

    baseline_rows = [r for r in rows if r.is_baseline]
    other = sorted([r for r in rows if not r.is_baseline], key=lambda x: x.rate)
    rows = baseline_rows + other

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
    """Совместимость с прежним вызовом из rates_summary_thb_rub."""
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
