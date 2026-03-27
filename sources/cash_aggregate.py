# -*- coding: utf-8 -*-
"""Объединение котировок наличных РБК и Banki.ru: один ряд на банк+курс."""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Dict, List, Optional, Tuple

from sources.banki_cash import (
    BANKI_REGIONS,
    banki_sell_rows,
    fetch_banki_banks_or_exchanges,
)
from sources.rbc_bank_title import canonical_bank_key, rbc_short_bank_name
from sources.rbc_cash_json import bank_sell_rows, fetch_cash_rates_json

# ISO 4217 numeric (как на Banki.ru)
BANKI_CURRENCY_ID: Dict[str, int] = {
    "USD": 840,
    "EUR": 978,
    "CNY": 156,
}

_SRC_RBC = "rbc"
_SRC_BANKI = "banki"


def rbc_cash_enabled() -> bool:
    """
    Временный флаг отключения РБК без изменения кода вызовов.
    Значения для отключения: 1/true/yes/on (в любом регистре).
    """
    raw = (os.environ.get("RATES_DISABLE_RBC") or "").strip().lower()
    return raw not in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CashOffer:
    sell: float
    bank_display: str
    sources: frozenset[str]

    def sources_label(self) -> str:
        parts: List[str] = []
        if _SRC_RBC in self.sources:
            parts.append("РБК")
        if _SRC_BANKI in self.sources:
            parts.append("Banki")
        return ", ".join(parts) if parts else ""


def _dedup_key(sell: float, display: str) -> Tuple[float, str]:
    return (round(sell, 2), canonical_bank_key(display))


def _collapse_offers(offers: List[CashOffer]) -> List[CashOffer]:
    """Одна строка на пару (округлённый sell, канонический банк) в рамках одного источника."""
    seen: set[Tuple[float, str]] = set()
    out: List[CashOffer] = []
    for o in sorted(offers, key=lambda x: (x.sell, x.bank_display.casefold())):
        k = _dedup_key(o.sell, o.bank_display)
        if k in seen:
            continue
        seen.add(k)
        out.append(o)
    return out


def _offers_from_rbc_banks(banks: Any) -> List[CashOffer]:
    out: List[CashOffer] = []
    for sell, raw in bank_sell_rows(banks):
        short = rbc_short_bank_name(raw)
        label = (short or raw or "—").strip()
        out.append(
            CashOffer(
                sell=sell,
                bank_display=label,
                sources=frozenset({_SRC_RBC}),
            )
        )
    return _collapse_offers(out)


def _offers_from_banki_payload(payload: Any) -> List[CashOffer]:
    out: List[CashOffer] = []
    for sell, name in banki_sell_rows(payload):
        label = (name or "—").strip()
        out.append(
            CashOffer(
                sell=sell,
                bank_display=label,
                sources=frozenset({_SRC_BANKI}),
            )
        )
    return _collapse_offers(out)


def _merge_rbc_and_banki(
    rbc_offers: List[CashOffer],
    banki_offers: List[CashOffer],
) -> List[CashOffer]:
    by_key: Dict[Tuple[float, str], CashOffer] = {}
    for o in banki_offers:
        k = _dedup_key(o.sell, o.bank_display)
        by_key[k] = o
    for o in rbc_offers:
        k = _dedup_key(o.sell, o.bank_display)
        if k not in by_key:
            by_key[k] = o
        else:
            prev = by_key[k]
            by_key[k] = CashOffer(
                sell=prev.sell,
                bank_display=o.bank_display,
                sources=prev.sources | o.sources,
            )
    return sorted(
        by_key.values(), key=lambda x: (x.sell, x.bank_display.casefold())
    )


def unified_top_sell_offers(
    *,
    fiat_code: str,
    banki_region_key: str,
    rbc_city_id: Optional[int],
    rbc_currency_id: int,
    top_n: int,
    timeout: float = 22.0,
    use_banki: bool = True,
) -> Tuple[List[CashOffer], List[str]]:
    """
    Топ ``top_n`` по продаже после объединения РБК (если ``rbc_city_id`` задан) и Banki.ru.

    ``banki_region_key`` — ключ из :data:`sources.banki_cash.BANKI_REGIONS`.
    """
    warnings: List[str] = []
    rbc_offers: List[CashOffer] = []
    if rbc_city_id is not None and rbc_cash_enabled():
        rbc_data = fetch_cash_rates_json(
            city=rbc_city_id, currency_id=rbc_currency_id, timeout=timeout
        )
        if isinstance(rbc_data, dict) and isinstance(rbc_data.get("banks"), list):
            rbc_offers = _offers_from_rbc_banks(rbc_data.get("banks"))
        else:
            warnings.append(f"РБК JSON: {fiat_code} (city {rbc_city_id})")

    banki_offers: List[CashOffer] = []
    if use_banki:
        cfg = BANKI_REGIONS.get(banki_region_key)
        cur_id = BANKI_CURRENCY_ID.get(fiat_code)
        if cfg is None or cur_id is None:
            warnings.append(
                f"Banki: нет региона «{banki_region_key}» или валюты {fiat_code}"
            )
        else:
            payload = fetch_banki_banks_or_exchanges(
                region_url=str(cfg["regionUrl"]),
                region_id=int(cfg["regionId"]),
                currency_id=cur_id,
                sort_attribute=str(cfg.get("sortAttribute") or "recommend"),
                order=str(cfg.get("order") or "desc"),
                timeout=timeout,
            )
            if payload is None:
                warnings.append(
                    f"Banki: нет ответа {fiat_code} ({cfg['regionUrl']})"
                )
            else:
                banki_offers = _offers_from_banki_payload(payload)

    merged = _merge_rbc_and_banki(rbc_offers, banki_offers)
    return merged[: max(0, top_n)], warnings
