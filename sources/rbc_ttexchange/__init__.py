# -*- coding: utf-8 -*-
"""
Цепочка наличных: минимальный курс продажи валюты (РБК, Москва/СПб) × курс TT Exchange (THB/ед.).
Итог в сводке — **RUB за 1 THB** (сопоставимо с прочими строками блока «Наличные RUB ➔ THB»).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from rates_sources import FetchContext, SourceCategory, SourceQuote

from sources.rbc_cash_json import fetch_cash_rates_json, min_sell_rub_per_unit
from sources.vbr_cash import VBR_ENDPOINTS, fetch_vbr_rates_html, vbr_sell_rows

SOURCE_ID = "rbc_ttexchange"
EMOJI = "•"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

# city_id → префикс label
_RBC_CITIES: Tuple[Tuple[int, str, str], ...] = (
    (1, "Москва", "moskva"),
    (2, "Санкт-Петербург", "sankt-peterburg"),
)

# (код валюты для label, currency в API РБК)
_RBC_FIAT: Tuple[Tuple[str, int], ...] = (
    ("USD", 3),
    ("EUR", 2),
    ("CNY", 423),
)

_CASH_RUB_SEQ_MSK = (100, 101, 102)
_CASH_RUB_SEQ_SPB = (200, 201, 202)


def help_text() -> str:
    return (
        "РБК/VBR cash (Москва, СПб) min sell × TT Exchange THB/USD|EUR|CNY → implied RUB/THB. "
        "См. rbc_ttexchange (без отдельного CLI)."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _ttex_thb_per_fiat() -> Tuple[Optional[Dict[str, float]], Dict[str, str], str]:
    from sources.ttexchange import _branch_display_name, _pick_currency_row
    from sources.ttexchange import ttexchange_api as ttx

    logger.info("rbc_ttexchange: TT Exchange get_stores + get_currencies (см. логи ttexchange http)")
    t0 = time.perf_counter()
    stores = ttx.get_stores("ru")
    bid = ttx._pick_default_branch_id(stores)
    if not bid:
        logger.info(
            "rbc_ttexchange: TT блок пуст за %.2fs (нет branch_id)",
            time.perf_counter() - t0,
        )
        return None, {}, ""
    branch_name = _branch_display_name(stores, bid)
    cur = ttx.get_currencies(bid, is_main=False)
    out: Dict[str, float] = {}
    notes: Dict[str, str] = {}
    for code in ("USD", "EUR", "CNY"):
        row, tier_note, omit_denoms = _pick_currency_row(cur, code)
        if not row:
            continue
        buy = row.get("current_buy_rate")
        if buy is None:
            continue
        try:
            thb = float(buy)
        except (TypeError, ValueError):
            continue
        if thb <= 0:
            continue
        out[code] = thb
        parts: List[str] = []
        if not omit_denoms:
            if tier_note:
                parts.append(tier_note)
            else:
                desc = row.get("description")
                if desc:
                    parts.append(str(desc))
        if branch_name:
            parts.append(branch_name)
        notes[code] = " · ".join(parts)
    logger.info(
        "rbc_ttexchange: TT блок готов за %.2fs (branch=%s)",
        time.perf_counter() - t0,
        branch_name or "—",
    )
    return out, notes, branch_name


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    rbc_disabled = (os.environ.get("RATES_DISABLE_RBC") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    vbr_disabled = (os.environ.get("RATES_DISABLE_VBR") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    thb_map, tt_notes, _branch = _ttex_thb_per_fiat()
    if not thb_map:
        ctx.warnings.append(
            "rbc_ttexchange: нет курсов USD/EUR/CNY у TT Exchange (филиал не выбран или API)"
        )
        return None

    quotes: List[SourceQuote] = []
    seq_m = list(_CASH_RUB_SEQ_MSK)
    seq_s = list(_CASH_RUB_SEQ_SPB)

    for city_id, city_label, banki_key in _RBC_CITIES:
        seqs = seq_m if city_id == 1 else seq_s
        for idx, (fiat_code, cur_id) in enumerate(_RBC_FIAT):
            thb_per = thb_map.get(fiat_code)
            if thb_per is None or thb_per <= 0:
                continue
            bank_name = ""
            source_tag = ""
            rub_per: Optional[float] = None

            if not rbc_disabled:
                logger.info(
                    "rbc_ttexchange: РБК JSON city_id=%s %s currency_id=%s (см. rbc_cash http)",
                    city_id,
                    city_label,
                    cur_id,
                )
                data = fetch_cash_rates_json(city=city_id, currency_id=cur_id)
                if not isinstance(data, dict):
                    ctx.warnings.append(
                        f"rbc_ttexchange: не удалось JSON РБК {fiat_code} {city_label}"
                    )
                    continue
                banks = data.get("banks")
                rub_per, bank_name = min_sell_rub_per_unit(banks)
                if rub_per is not None and rub_per > 0:
                    source_tag = "РБК"

            if rub_per is None or rub_per <= 0:
                if vbr_disabled:
                    ctx.warnings.append(
                        f"rbc_ttexchange: нет min sell {fiat_code} для {city_label} (РБК), VBR отключен"
                    )
                    continue
                if banki_key not in VBR_ENDPOINTS:
                    ctx.warnings.append(
                        f"rbc_ttexchange: нет VBR endpoint {fiat_code} для {city_label}"
                    )
                    continue
                html = fetch_vbr_rates_html(
                    banki_key,
                    fiat_code,
                    timeout=15.0,
                )
                rows = vbr_sell_rows(html or "", fiat_code) if html else []
                if not rows:
                    ctx.warnings.append(
                        f"rbc_ttexchange: нет min sell {fiat_code} для {city_label} (VBR)"
                    )
                    continue
                rub_per, bank_name = rows[0]
                source_tag = "VBR"

            implied = rub_per / thb_per
            if implied <= 0:
                continue
            label = f"{city_label} ({source_tag}) {fiat_code} ➔ TT"
            tt_part = tt_notes.get(fiat_code, "")
            rbc_part = f"{rub_per:g} RUB"
            # if bank_name:
            #     rbc_part += f", {bank_name}"
            thb_part = f"{thb_per:g} THB"
            # if tt_part:
            #     thb_part += f" ({tt_part})"
            note = f"{rbc_part}; {thb_part}"
            quotes.append(
                SourceQuote(
                    implied,
                    label,
                    note=note,
                    category=SourceCategory.CASH_RUB,
                    emoji="•",
                    compare_to_baseline=True,
                    cash_rub_seq=seqs[idx],
                )
            )

    return quotes if quotes else None
