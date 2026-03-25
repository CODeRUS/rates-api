# -*- coding: utf-8 -*-
"""Категории источников без зависимости от :mod:`rates_sources` / :mod:`sources` (импорт без циклов)."""
from __future__ import annotations

from enum import Enum


class SourceCategory(Enum):
    """Категория источника: переводы vs наличные в обменнике (по валюте наличных)."""

    TRANSFER = "transfer"
    CASH_RUB = "cash_rub"
    CASH_USD = "cash_usd"
    CASH_EUR = "cash_eur"
    CASH_CNY = "cash_cny"
