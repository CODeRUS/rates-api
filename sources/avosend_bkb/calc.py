# -*- coding: utf-8 -*-
"""Avosend RUB→USD (карта) × BBL TT: вспомогательные расчёты без HTTP."""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def parse_api_number(value: Any) -> Optional[float]:
    """Число из поля JSON Avosend (строка с `,` или `.` как в EU/US)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if v == v and v != float("inf") and v != float("-inf") else None
    s = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return v if v == v else None


def fee_and_convert_rate(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Поля ``fee`` и ``convertRate`` из ответа :func:`~sources.avosend.avosend_commission.fetch_commission`."""
    fee = parse_api_number(data.get("fee"))
    cr = parse_api_number(data.get("convertRate"))
    return fee, cr


def rub_per_thb(
    rub_amount: float,
    fee: float,
    convert_rate: float,
    thb_per_usd: float,
) -> Optional[float]:
    """
    RUB за 1 THB по цепочке: ``usd = (rub - fee) * convertRate``, ``thb = usd * thb_per_usd``,
    курс сводки ``rub_amount / thb`` (как у Avosend THB при использовании from/to).
    """
    if rub_amount <= 0 or thb_per_usd <= 0:
        return None
    net = rub_amount - fee
    if net <= 0:
        return None
    usd_amount = net * convert_rate
    if usd_amount <= 0:
        return None
    thb_amount = usd_amount * thb_per_usd
    if thb_amount <= 0:
        return None
    return rub_amount / thb_amount
