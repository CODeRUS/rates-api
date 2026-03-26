# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Pattern


@dataclass(frozen=True)
class CurrencyRule:
    currency: str
    category: str
    pattern: str


@dataclass(frozen=True)
class SourceConfig:
    source_id: str
    name: str
    chat: str
    emoji: str
    currencies: tuple[CurrencyRule, ...]


@dataclass(frozen=True)
class CompiledRule:
    currency: str
    category: str
    regex: Pattern[str]


@dataclass(frozen=True)
class ParsedRate:
    source_id: str
    source_name: str
    currency: str
    category: str
    rate: float
    message_id: int
    message_unix: float
    chat: str

