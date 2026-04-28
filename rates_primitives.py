# -*- coding: utf-8 -*-
"""
Примитивы для сводки: один HTTP-набор на ключ, затем источники только считают формулы.

Ключи ``prim:*`` живут в ``doc[\"prim\"]`` (см. :mod:`rates_unified_cache`).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import rates_unified_cache as ucc

logger = logging.getLogger(__name__)

# --- Ключи (стабильные, без ctx_digest) ---
PRIM_BYBIT_P2P_RUB = "prim:bybit:p2p_rub_usdt:v1"
PRIM_HTX_P2P_RUB = "prim:htx:p2p_rub_usdt:v1"
PRIM_BITKUB_USDT_THB = "prim:bitkub:usdt_thb_highest_bid:v1"
PRIM_BINANCE_TH_USDT_BID = "prim:binance_th:usdt_thb_bid:v1"
PRIM_NOVAWALLET_LEDGER = "prim:novawallet:ledger_bundle:v1"
PRIM_MORETA_EXCHANGE_RATES = "prim:moreta:exchange_rates:v1"
# Курс Сбер QR (cron → unified prim); без HTTP в ensure_primitives.
PRIM_SBER_QR_TRANSFER = "prim:sber_qr_transfer"

# --- TTL ---
TTL_PRIM_BYBIT = ucc.TTL_L1_RATE_SOURCE_BYBIT_SEC
TTL_PRIM_HTX = ucc.TTL_L1_RATE_SOURCE_BYBIT_SEC
TTL_PRIM_BITKUB = ucc.TTL_L1_RATE_SOURCE_BYBIT_SEC
TTL_PRIM_BINANCE_TH = ucc.TTL_L1_RATE_SOURCE_BYBIT_SEC


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


TTL_PRIM_NOVAWALLET = _env_int("RATES_UNIFIED_TTL_PRIM_NOVAWALLET", 3600)
TTL_PRIM_MORETA = _env_int("RATES_UNIFIED_TTL_PRIM_MORETA", 600)

# Источник -> какие примитивы нужны (порядок не важен)
PRIMITIVE_KEYS_BY_SOURCE_ID: Dict[str, Tuple[str, ...]] = {
    "bybit_bitkub": (PRIM_BYBIT_P2P_RUB, PRIM_BITKUB_USDT_THB),
    "bybit_binanceth": (PRIM_BYBIT_P2P_RUB, PRIM_BINANCE_TH_USDT_BID),
    "htx_bitkub": (PRIM_HTX_P2P_RUB, PRIM_BITKUB_USDT_THB),
    "htx_binanceth": (PRIM_HTX_P2P_RUB, PRIM_BINANCE_TH_USDT_BID),
    "bybit_novawallet": (PRIM_BYBIT_P2P_RUB, PRIM_NOVAWALLET_LEDGER),
    "bybit_moreta": (PRIM_BYBIT_P2P_RUB, PRIM_MORETA_EXCHANGE_RATES),
    "sberbank_qr": (PRIM_SBER_QR_TRANSFER,),
}

# Если в unified появился примитив, а старая L2-сводка не перечисляет его в deps — пересобрать.
SUMMARY_L2_ORPHAN_PRIM_INVALIDATE: Tuple[str, ...] = (PRIM_SBER_QR_TRANSFER,)


def primitive_keys_for_sources(source_ids: Sequence[str]) -> List[str]:
    out: Set[str] = set()
    for sid in source_ids:
        for k in PRIMITIVE_KEYS_BY_SOURCE_ID.get(sid, ()):
            out.add(k)
    return sorted(out)


def _fetch_bybit_p2p_payload() -> Dict[str, Any]:
    from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp

    w: List[str] = []
    try:
        ia, ib = bp.fetch_best_cash_and_bank_transfer_items(
            size=20,
            verification_filter=0,
            target_usdt=bp.DEFAULT_TARGET_USDT,
            min_completion=99.0,
        )
    except RuntimeError as e:
        return {"cash_price": None, "transfer_price": None, "warnings": [f"Bybit P2P: {e}"]}
    cash_p = float(ia["price"]) if ia else None
    tr_p = float(ib["price"]) if ib else None
    if ia is None:
        w.append(
            "Bybit: нет объявлений Cash Deposit (18) с completion≥99 "
            f"({bp.DEFAULT_TARGET_USDT:g} USDT, minAmount≥{bp.DEFAULT_TARGET_USDT:g}·price)"
        )
    if ib is None:
        w.append(
            "Bybit: нет объявлений только перевод (14, без 18) с completion≥99 "
            f"({bp.DEFAULT_TARGET_USDT:g} USDT, minAmount≥{bp.DEFAULT_TARGET_USDT:g}·price)"
        )
    return {"cash_price": cash_p, "transfer_price": tr_p, "warnings": w}


def _fetch_htx_p2p_payload() -> Dict[str, Any]:
    from sources.htx_bitkub import htx_p2p_usdt_rub as hx

    w: List[str] = []
    try:
        ha, hb = hx.fetch_best_cash_and_non_cash_offers(max_pages=30)
    except RuntimeError as e:
        return {"cash_price": None, "transfer_price": None, "warnings": [f"HTX OTC: {e}"]}
    cash_p = float(ha["price"]) if ha else None
    tr_p = float(hb["price"]) if hb else None
    if ha is None:
        w.append(
            "HTX: нет объявлений с наличными под фильтры "
            "(100 USDT, minTradeLimit≥100·price)"
        )
    if hb is None:
        w.append(
            "HTX: нет объявлений без наличных под фильтры "
            "(100 USDT, minTradeLimit≥100·price)"
        )
    return {"cash_price": cash_p, "transfer_price": tr_p, "warnings": w}


def _fetch_bitkub_payload() -> Dict[str, Any]:
    from sources.bybit_bitkub import bitkub_usdt_thb as bk

    w: List[str] = []
    try:
        tk = bk.fetch_ticker()
    except RuntimeError as e:
        return {"highest_bid": None, "warnings": [f"Bitkub: {e}"]}
    b = float(tk.get("highestBid") or 0)
    if b <= 0:
        w.append("Bitkub: нет highestBid для USDT")
        return {"highest_bid": None, "warnings": w}
    return {"highest_bid": b, "warnings": w}


def _fetch_binance_th_payload() -> Dict[str, Any]:
    from sources.binance_th.usdt_thb_book import fetch_bid_thb_per_usdt

    w: List[str] = []
    try:
        v = fetch_bid_thb_per_usdt()
    except RuntimeError as e:
        return {"bid_thb_per_usdt": None, "warnings": [f"Binance TH: {e}"]}
    return {"bid_thb_per_usdt": v, "warnings": w}


def _fetch_novawallet_payload() -> Dict[str, Any]:
    from sources.bybit_novawallet.novawallet_api import (
        fetch_cashout_fee_usd,
        fetch_thb_per_usdt,
    )

    w: List[str] = []
    r_thb, wr = fetch_thb_per_usdt()
    if wr:
        w.append(wr)
    fee_usd, wf = fetch_cashout_fee_usd()
    if wf:
        w.append(wf)
    return {
        "thb_per_usdt": r_thb,
        "cashout_usd": fee_usd,
        "warnings": w,
    }


def _fetch_moreta_payload() -> Dict[str, Any]:
    from sources.bybit_moreta.moreta_api import fetch_thb_per_usdt

    w: List[str] = []
    r_thb, wr = fetch_thb_per_usdt()
    if wr:
        w.append(wr)
    return {"thb_per_usdt": r_thb, "warnings": w}


_FETCHERS: Dict[str, Callable[[], Dict[str, Any]]] = {
    PRIM_BYBIT_P2P_RUB: _fetch_bybit_p2p_payload,
    PRIM_HTX_P2P_RUB: _fetch_htx_p2p_payload,
    PRIM_BITKUB_USDT_THB: _fetch_bitkub_payload,
    PRIM_BINANCE_TH_USDT_BID: _fetch_binance_th_payload,
    PRIM_NOVAWALLET_LEDGER: _fetch_novawallet_payload,
    PRIM_MORETA_EXCHANGE_RATES: _fetch_moreta_payload,
}

_TTL_FOR_KEY: Dict[str, int] = {
    PRIM_BYBIT_P2P_RUB: TTL_PRIM_BYBIT,
    PRIM_HTX_P2P_RUB: TTL_PRIM_HTX,
    PRIM_BITKUB_USDT_THB: TTL_PRIM_BITKUB,
    PRIM_BINANCE_TH_USDT_BID: TTL_PRIM_BINANCE_TH,
    PRIM_NOVAWALLET_LEDGER: TTL_PRIM_NOVAWALLET,
    PRIM_MORETA_EXCHANGE_RATES: TTL_PRIM_MORETA,
}


async def ensure_primitives(
    doc: Dict[str, Any],
    keys: Sequence[str],
    *,
    refresh: bool,
    max_concurrent: int = 8,
) -> Set[str]:
    """
    Параллельно (async + thread pool) подгружает примитивы в ``doc[\"prim\"]``.

    При ``refresh=False`` пропускает ключ, если :func:`~rates_unified_cache.prim_get_valid`.

    Возвращает множество ключей, для которых был выполнен fetch (обновление в ``doc``).
    """
    refetched: Set[str] = set()
    if not keys:
        return refetched
    loop = asyncio.get_running_loop()
    sem = asyncio.Semaphore(max(1, int(max_concurrent)))
    locks = {k: asyncio.Lock() for k in keys}

    async def one(key: str) -> None:
        fetcher = _FETCHERS.get(key)
        ttl = _TTL_FOR_KEY.get(key, 60)
        if fetcher is None:
            return
        async with locks[key]:
            if not refresh and ucc.prim_get_valid(doc, key) is not None:
                logger.debug("primitive skip (cache hit) %s", key)
                return
            logger.info("primitive fetch start %s", key)
            t0 = time.perf_counter()
            async with sem:
                try:
                    payload = await loop.run_in_executor(None, fetcher)
                except Exception:
                    logger.exception("primitive fetch failed %s", key)
                    raise
            ucc.prim_set(doc, key, payload, ttl_sec=ttl)
            refetched.add(key)
            logger.info(
                "primitive fetch done %s in %.2fs",
                key,
                time.perf_counter() - t0,
            )

    await asyncio.gather(*(one(k) for k in keys))
    return refetched


def run_ensure_primitives(
    doc: Dict[str, Any],
    keys: Sequence[str],
    *,
    refresh: bool,
    max_concurrent: Optional[int] = None,
) -> Set[str]:
    """Обёртка ``asyncio.run(ensure_primitives(...))`` для синхронного кода."""
    if not keys:
        return set()
    from rates_parallel import default_max_workers

    n = max_concurrent if max_concurrent is not None else _env_int("RATES_PRIM_MAX_CONCURRENT", 0)
    if n <= 0:
        n = default_max_workers()
    return asyncio.run(
        ensure_primitives(doc, keys, refresh=refresh, max_concurrent=n)
    )


# --- Чтение для источников (при отсутствии prim — вернуть None и позволить fallback) ---


def read_bybit_p2p(doc: Optional[Dict[str, Any]]) -> Tuple[
    Optional[float], Optional[float], List[str]
]:
    """(cash RUB/USDT, transfer RUB/USDT, warnings из примитива)."""
    if not doc:
        return None, None, []
    hit = ucc.prim_get_valid(doc, PRIM_BYBIT_P2P_RUB)
    if hit is None:
        return None, None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, None, []
    cp = p.get("cash_price")
    tp = p.get("transfer_price")
    c_f = float(cp) if isinstance(cp, (int, float)) and float(cp) > 0 else None
    t_f = float(tp) if isinstance(tp, (int, float)) and float(tp) > 0 else None
    w = list(p.get("warnings") or [])
    return c_f, t_f, w


def read_htx_p2p(doc: Optional[Dict[str, Any]]) -> Tuple[
    Optional[float], Optional[float], List[str]
]:
    if not doc:
        return None, None, []
    hit = ucc.prim_get_valid(doc, PRIM_HTX_P2P_RUB)
    if hit is None:
        return None, None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, None, []
    cp = p.get("cash_price")
    tp = p.get("transfer_price")
    c_f = float(cp) if isinstance(cp, (int, float)) and float(cp) > 0 else None
    t_f = float(tp) if isinstance(tp, (int, float)) and float(tp) > 0 else None
    w = list(p.get("warnings") or [])
    return c_f, t_f, w


def read_bitkub_bid(doc: Optional[Dict[str, Any]]) -> Tuple[Optional[float], List[str]]:
    if not doc:
        return None, []
    hit = ucc.prim_get_valid(doc, PRIM_BITKUB_USDT_THB)
    if hit is None:
        return None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, []
    hb = p.get("highest_bid")
    v = float(hb) if isinstance(hb, (int, float)) and float(hb) > 0 else None
    return v, list(p.get("warnings") or [])


def read_binance_th_bid(doc: Optional[Dict[str, Any]]) -> Tuple[Optional[float], List[str]]:
    if not doc:
        return None, []
    hit = ucc.prim_get_valid(doc, PRIM_BINANCE_TH_USDT_BID)
    if hit is None:
        return None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, []
    b = p.get("bid_thb_per_usdt")
    v = float(b) if isinstance(b, (int, float)) and float(b) > 0 else None
    return v, list(p.get("warnings") or [])


def read_moreta_thb_per_usdt(
    doc: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], List[str]]:
    """THB за 1 USDT из примитива Moreta (поле ``rates.USD_THB``)."""
    if not doc:
        return None, []
    hit = ucc.prim_get_valid(doc, PRIM_MORETA_EXCHANGE_RATES)
    if hit is None:
        return None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, []
    r = p.get("thb_per_usdt")
    v = float(r) if isinstance(r, (int, float)) and float(r) > 0 else None
    return v, list(p.get("warnings") or [])


def read_novawallet_bundle(
    doc: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], Optional[float], List[str]]:
    """(thb_per_usdt, cashout_usd или None, warnings)."""
    if not doc:
        return None, None, []
    hit = ucc.prim_get_valid(doc, PRIM_NOVAWALLET_LEDGER)
    if hit is None:
        return None, None, []
    p = hit[1]
    if not isinstance(p, dict):
        return None, None, []
    r = p.get("thb_per_usdt")
    f = p.get("cashout_usd")
    r_f = float(r) if isinstance(r, (int, float)) and float(r) > 0 else None
    f_f = float(f) if isinstance(f, (int, float)) and f is not None and float(f) >= 0 else None
    return r_f, f_f, list(p.get("warnings") or [])


def combined_bybit_min_rub_per_usdt(doc: Optional[Dict[str, Any]]) -> Optional[float]:
    c, t, _ = read_bybit_p2p(doc)
    opts = [x for x in (c, t) if x is not None and x > 0]
    return min(opts) if opts else None
