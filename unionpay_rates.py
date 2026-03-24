#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Курсы UnionPay International (публичный JSON с сайта запроса курса).

Источник (как на странице /en/rate/): файл за дату Beijing/системную публикацию
``https://www.unionpayintl.com/upload/jfimg/YYYYMMDD.json``

В каждой записи ``exchangeRateJson``:
  * ``transCur`` — валюта транзакции
  * ``baseCur`` — базовая валюта котировки
  * ``rateData`` — число: 1 единица transCur = rateData единиц baseCur

Пример: transCur=THB, baseCur=CNY, rateData≈0.21 → 1 THB = 0.21 CNY.
"""

from __future__ import annotations

import json
import ssl
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

UNIONPAY_DAILY_JSON = "https://www.unionpayintl.com/upload/jfimg/{yyyymmdd}.json"
USER_AGENT = "unionpay-rates/1.0 (python; research)"


def _get_json(url: str, *, timeout: float = 45.0) -> Any:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_daily_file(d: Optional[date] = None, *, timeout: float = 45.0) -> Dict[str, Any]:
    """Скачивает JSON на дату ``d`` (по умолчанию сегодня, UTC/локаль — как у сервера)."""
    if d is None:
        d = date.today()
    ymd = f"{d.year:04d}{d.month:02d}{d.day:02d}"
    url = UNIONPAY_DAILY_JSON.format(yyyymmdd=ymd)
    data = _get_json(url, timeout=timeout)
    if not isinstance(data, dict) or "exchangeRateJson" not in data:
        raise ValueError(f"Неожиданная структура ответа: {url}")
    return data


def build_index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], float]:
    """Индекс (trans_cur, base_cur) -> rateData."""
    out: Dict[Tuple[str, str], float] = {}
    for row in rows:
        tc = str(row["transCur"]).upper()
        bc = str(row["baseCur"]).upper()
        out[(tc, bc)] = float(row["rateData"])
    return out


def rate_trans_to_base(
    trans: str,
    base: str,
    *,
    d: Optional[date] = None,
    cache: Optional[Dict[str, Any]] = None,
) -> float:
    """
    Сколько единиц ``base`` за 1 единицу ``trans`` (как в файле UnionPay).

    Если ``cache`` передан (результат :func:`fetch_daily_file`), повторно сеть не ходит.
    """
    data = cache if cache is not None else fetch_daily_file(d)
    idx = build_index(data["exchangeRateJson"])
    key = (trans.upper(), base.upper())
    if key not in idx:
        raise KeyError(f"Нет пары {trans}->{base} в файле на дату")
    return idx[key]


def thb_per_cny(d: Optional[date] = None, cache: Optional[Dict[str, Any]] = None) -> float:
    """1 CNY = ? THB (trans CNY, base THB)."""
    return rate_trans_to_base("CNY", "THB", d=d, cache=cache)


def cny_per_thb(d: Optional[date] = None, cache: Optional[Dict[str, Any]] = None) -> float:
    """
    1 THB = ? CNY — запись ``exchangeRateJson`` с ``transCur`` THB и ``baseCur`` CNY
    (поле ``rateData``, как в публичном дневном JSON UnionPay).
    """
    return rate_trans_to_base("THB", "CNY", d=d, cache=cache)


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="UnionPay daily JSON helper")
    p.add_argument("--date", help="YYYY-MM-DD", default=None)
    p.add_argument("--trans", default="THB")
    p.add_argument("--base", default="CNY")
    args = p.parse_args()
    dt = date.fromisoformat(args.date) if args.date else None
    raw = fetch_daily_file(dt)
    v = rate_trans_to_base(args.trans, args.base, cache=raw)
    print(f"1 {args.trans} = {v} {args.base}")
    print(f"1 CNY = {thb_per_cny(cache=raw)} THB")
