#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сводка курсов **RUB за 1 THB** (направление **RUB → THB**: сколько рублей отдаёте за бат).

Агрегирует данные из модулей проекта (Forex **Xe midmarket**, Bybit+Bitkub, РСХБ/UnionPay,
KwikPay, Korona, Avosend, ex24, askmoney). Результаты **кешируются на 30 минут** в файле рядом со скриптом.

Пример::

    python rates_summary_thb_rub.py
    python rates_summary_thb_rub.py --refresh
    python rates_summary_thb_rub.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

import koronapay_tariffs as _korona_ref

CACHE_FILE = _SCRIPT_DIR / ".rates_summary_cache.json"
CACHE_TTL_SEC = 30 * 60
CACHE_VERSION = 13


def _fmt_money_ru(n: float) -> str:
    """Группы разрядов пробелом, без ломки других запятых в строке."""
    return f"{n:,.0f}".replace(",", " ")


# Референсные суммы (как в fx_reports / ваших примерах)
DEFAULT_THB_REF = 30_000.0
DEFAULT_ATM_FEE_THB = 250.0
# «Крупная» Korona: запрос API по сумме **получения** в THB.
DEFAULT_KORONA_LARGE_THB = 40_000.0
# «Малые»: на 1 RUB ниже порога лучшего тарифа Korona (отправка).
DEFAULT_KORONA_SMALL_RUB = float(_korona_ref.RUB_MIN_SENDING_FOR_BEST_TIER) - 1.0
DEFAULT_AVOSEND_RUB = 10_000.0

@dataclass
class RateRow:
    """Одна строка сводки."""

    rate: float  # RUB за 1 THB
    label: str
    emoji: str
    note: str = ""  # доп. текст в конце строки (в скобках или через «|»)
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


def collect_rows(
    *,
    thb_ref: float,
    atm_fee: float,
    korona_small_rub: float,
    korona_large_thb: float,
    avosend_rub: float,
    unionpay_date: Optional[str],
    moex_override: Optional[float],
) -> Tuple[List[RateRow], float, List[str]]:
    import askmoney_rub_thb as am
    import avosend_commission as av
    import bitkub_usdt_thb as bk
    import bybit_p2p_usdt_rub as bp
    import card_fx_calculator as cfx
    import ex24_rub_thb as e24
    import forex_xe_api as xe
    import koronapay_tariffs as kp
    import kwikpay_rates as kw

    warnings: List[str] = []
    rows: List[RateRow] = []

    on = date.fromisoformat(unionpay_date) if unionpay_date else None

    # --- Forex (база для процентов): Xe midmarket THB → RUB ---
    forex: Optional[float] = None
    try:
        conv = xe.midmarket_convert("THB", "RUB", 1.0)
        forex = float(conv["result"])
        rows.append(
            RateRow(
                rate=forex,
                label="Forex",
                emoji="📈",
                note="",
                is_baseline=True,
            )
        )
    except Exception as e:
        warnings.append(f"Forex (Xe): {e}")

    baseline = forex if forex is not None and forex > 0 else 2.5

    # --- Live UnionPay + MOEX + РСХБ ---
    cpt: float = 0.0
    moex: float = 0.0
    rshb_sell: float = 0.0
    rshb_app: float = 0.0
    try:
        (
            cpt,
            moex,
            sell_dec,
            _,
            sell_online_dec,
            _,
            live_stale,
            _,
        ) = cfx.fetch_live_inputs(on, moex_override)
        rshb_sell = float(sell_dec)
        rshb_app = float(sell_online_dec)
        if live_stale:
            warnings.append(
                "РСХБ/UnionPay/MOEX: таймаут сети — в расчётах использованы "
                f"последние сохранённые курсы ({cfx.LIVE_INPUTS_CACHE_FILE.name})."
            )
    except Exception as e:
        warnings.append(f"РСХБ/UnionPay/MOEX: {e}")

    broker_cny_rub = moex if moex else 0.0

    if cpt > 0 and broker_cny_rub > 0:
        rows.append(
            RateRow(
                rate=cfx.rub_per_thb(cpt, broker_cny_rub),
                label="РСХБ UP CNY (брокер, оплата)",
                emoji="💳",
                note="",
            )
        )
    if cpt > 0 and rshb_app > 0:
        rows.append(
            RateRow(
                rate=cfx.rub_per_thb(cpt, rshb_app),
                label="РСХБ UP CNY (приложение, оплата)",
                emoji="💳",
                note="",
            )
        )

    if cpt > 0 and rshb_sell > 0:
        rows.append(
            RateRow(
                rate=cfx.rub_per_thb(cpt, rshb_sell),
                label="РСХБ UP RUB (оплата)",
                emoji="💳",
                note="",
            )
        )

    if cpt > 0 and broker_cny_rub > 0:
        rub_atm, rpt = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            broker_cny_rub,
            issuer_fee_on_cny_base=0.03,
        )
        rows.append(
            RateRow(
                rate=rpt,
                label=f"РСХБ UP CNY (брокер, снятие {thb_ref:.0f}+{atm_fee:.0f})",
                emoji="🏧",
                note="",
            )
        )
        if rshb_app > 0:
            rub_atm2, rpt2 = cfx.atm_rub_from_cny_path(
                thb_ref,
                atm_fee,
                cpt,
                rshb_app,
                issuer_fee_on_cny_base=0.03,
            )
            rows.append(
                RateRow(
                    rate=rpt2,
                    label=f"РСХБ UP CNY (приложение, снятие {thb_ref:.0f}+{atm_fee:.0f})",
                    emoji="🏧",
                    note="",
                )
            )

    if cpt > 0 and rshb_sell > 0:
        rub_rc, rpt_rc = cfx.atm_rub_from_cny_path(
            thb_ref,
            atm_fee,
            cpt,
            rshb_sell,
            issuer_fee_on_cny_base=0.0,
            extra_rub_pct_of_base=0.01,
        )
        rows.append(
            RateRow(
                rate=rpt_rc,
                label=f"РСХБ UP RUB (снятие {thb_ref:.0f}+{atm_fee:.0f})",
                emoji="🏧",
                note="",
            )
        )

    # --- Bybit P2P (купить USDT) + Bitkub (продать USDT → THB) — две строки ---
    try:
        items = bp.fetch_all_online_items(size=20, verification_filter=0)
        a = bp.filter_cash_deposit_to_bank(items, 99.0)
        b = bp.filter_bank_transfer_no_cash(items, 99.0)
        ia = bp.min_by_price(a)
        ib = bp.min_by_price(b)
        tk = bk.fetch_ticker()
        thb_usdt = float(tk.get("highestBid") or 0)
        if thb_usdt > 0:
            if ia:
                rub_cd = float(ia["price"])
                rows.append(
                    RateRow(
                        rate=rub_cd / thb_usdt,
                        label="Bybit P2P (cash) → Bitkub",
                        emoji="💸",
                        note="",
                    )
                )
            else:
                warnings.append("Bybit: нет объявлений Cash Deposit (18) с completion≥99")
            if ib:
                rub_bt = float(ib["price"])
                rows.append(
                    RateRow(
                        rate=rub_bt / thb_usdt,
                        label="Bybit P2P (перевод) → Bitkub",
                        emoji="💸",
                        note="",
                    )
                )
            else:
                warnings.append("Bybit: нет объявлений только перевод (14, без 18) с completion≥99")
        else:
            warnings.append("Bitkub: нет highestBid для USDT")
    except Exception as e:
        warnings.append(f"Bybit/Bitkub: {e}")

    # --- Korona ---
    def _korona_send(rub_amt: float, label: str) -> None:
        try:
            rows_kp = kp.fetch_tariffs(
                sending_amount_kopecks=kp.rub_to_kopecks(rub_amt),
            )
            row = rows_kp[0]
            rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
            thb = kp.satang_to_thb(int(row["receivingAmount"]))
            if thb > 0:
                rows.append(
                    RateRow(
                        rate=rub / thb,
                        label=label,
                        emoji="💱",
                        note="",
                    )
                )
        except Exception as e:
            warnings.append(f"Korona {label}: {e}")

    def _korona_recv(thb_amt: float, label: str) -> None:
        try:
            rows_kp = kp.fetch_tariffs(
                receiving_amount_satang=kp.thb_to_satang(thb_amt),
            )
            row = rows_kp[0]
            rub = kp.kopecks_to_rub(int(row["sendingAmount"]))
            thb = kp.satang_to_thb(int(row["receivingAmount"]))
            if thb > 0:
                rows.append(
                    RateRow(
                        rate=rub / thb,
                        label=label,
                        emoji="💱",
                        note="",
                    )
                )
        except Exception as e:
            warnings.append(f"Korona {label}: {e}")

    _korona_recv(
        korona_large_thb,
        f"Korona (от {_fmt_money_ru(korona_large_thb)} THB)",
    )
    _korona_send(korona_small_rub, "Korona (малые суммы)")

    # --- Avosend (одна строка, если курс счёта и наличных совпадает) ---
    def _avo_rate(mode: av.TransferMode) -> Optional[float]:
        try:
            d = av.fetch_commission(avosend_rub, mode)
            fr = float(d.get("from"))
            to = float(d.get("to"))
            if to > 0:
                return fr / to
        except Exception as e:
            warnings.append(f"Avosend {mode.value}: {e}")
        return None

    r_bank = _avo_rate(av.TransferMode.BANK_ACCOUNT)
    r_cash = _avo_rate(av.TransferMode.CASH)
    avo_label = f"Avosend (от {_fmt_money_ru(avosend_rub)} RUB)"
    if r_bank is not None and r_cash is not None:
        if abs(r_bank - r_cash) <= max(1e-9, 1e-9 * abs(r_bank)):
            rows.append(RateRow(rate=r_bank, label=avo_label, emoji="💱", note=""))
        else:
            rows.append(
                RateRow(
                    rate=r_bank,
                    label="Avosend на счёт",
                    emoji="💱",
                    note=f"от {_fmt_money_ru(avosend_rub)} RUB",
                )
            )
            rows.append(
                RateRow(
                    rate=r_cash,
                    label="Avosend наличные",
                    emoji="💱",
                    note=f"от {_fmt_money_ru(avosend_rub)} RUB",
                )
            )
    elif r_bank is not None:
        rows.append(
            RateRow(
                rate=r_bank,
                label="Avosend на счёт",
                emoji="💱",
                note=f"от {_fmt_money_ru(avosend_rub)} RUB",
            )
        )
    elif r_cash is not None:
        rows.append(
            RateRow(
                rate=r_cash,
                label="Avosend наличные",
                emoji="💱",
                note=f"от {_fmt_money_ru(avosend_rub)} RUB",
            )
        )

    # --- ex24: курс при минимальной наценке 0 % (с суммы RUB_MIN_FOR_ZERO_MARKUP) ---
    try:
        rr = e24.try_fetch_real_rate_rub_thb() or e24.DEFAULT_REAL_RATE
        rub_best = float(e24.RUB_MIN_FOR_ZERO_MARKUP)
        r_ex = e24.customer_rate_rub_per_thb(rub_best, rr)
        rows.append(
            RateRow(
                rate=r_ex,
                label="Ex24.pro",
                emoji="🤑",
                note=f"от {_fmt_money_ru(rub_best)} RUB",
            )
        )
    except Exception as e:
        warnings.append(f"ex24: {e}")

    # --- KwikPay: эффективный RUB/THB при сумме без комиссии (поле amount ≥ 30001) ---
    try:
        kq = kw.fetch_quotes_for_amounts([30_001])
        if kq:
            q = kq[0]
            if q.withdraw_thb > 0:
                if q.fee_rub != 0:
                    warnings.append(
                        f"KwikPay: при amount=30001 комиссия не 0 ({q.fee_rub:g} RUB), курс всё же выведен"
                    )
                rows.append(
                    RateRow(
                        rate=q.rub_per_thb,
                        label="KwikPay (от 30001 RUB)",
                        emoji="💱",
                        note="",
                    )
                )
    except Exception as e:
        warnings.append(f"KwikPay: {e}")

    # --- askmoney: лучший курс в модели и первая сумма, где он достигается ---
    try:
        html = am.fetch_homepage_html()
        params = am.parse_params_from_html(html)
        best_rub, _best_thb, _brt = am.min_effective_rate_rub_per_thb(params)
        thb_at = am.rub_to_thb(best_rub, params)
        rt = am.effective_rate_rub_per_thb(best_rub, thb_at)
        if rt is not None:
            rows.append(
                RateRow(
                    rate=rt,
                    label="askmoney.pro",
                    emoji="🤑",
                    note=f"от {_fmt_money_ru(best_rub)} RUB",
                )
            )
    except Exception as e:
        warnings.append(f"askmoney: {e}")

    # Убрать дубликаты по (label, note, emoji) — оставить лучший (мин.) курс
    dedup: Dict[Tuple[str, str, str], RateRow] = {}
    for row in rows:
        key = (row.label, row.note, row.emoji)
        if key not in dedup or row.rate < dedup[key].rate:
            dedup[key] = row
    rows = list(dedup.values())

    # Сортировка: база Forex первая, остальные по возрастанию курса
    baseline_rows = [r for r in rows if r.is_baseline]
    other = sorted([r for r in rows if not r.is_baseline], key=lambda x: x.rate)
    rows = baseline_rows + other

    return rows, baseline, warnings


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
        if not any(r.is_baseline for r in rows) and baseline > 0:
            pass
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

    print("THB/RUB (RUB ➔ THB)")
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
