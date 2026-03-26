# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Iterable, List

from userbot.models import CompiledRule, ParsedRate, SourceConfig


def compile_rules(cfg: SourceConfig) -> tuple[CompiledRule, ...]:
    out: List[CompiledRule] = []
    for r in cfg.currencies:
        out.append(
            CompiledRule(
                currency=r.currency.upper(),
                category=r.category.lower(),
                regex=re.compile(r.pattern, re.IGNORECASE | re.MULTILINE),
            )
        )
    return tuple(out)


def _to_float(s: str) -> float:
    return float((s or "").replace(" ", "").replace(",", "."))


def parse_message(
    *,
    source_id: str,
    source_name: str,
    chat: str,
    city: str,
    rules: Iterable[CompiledRule],
    text: str,
    message_id: int,
    message_unix: float,
) -> list[ParsedRate]:
    rows: List[ParsedRate] = []
    for r in rules:
        m = r.regex.search(text or "")
        if not m:
            continue
        val_raw = m.groupdict().get("rate")
        if val_raw is None:
            continue
        try:
            rate = _to_float(val_raw)
        except ValueError:
            continue
        if rate <= 0:
            continue
        rows.append(
            ParsedRate(
                source_id=source_id,
                source_name=source_name,
                currency=r.currency,
                category=r.category,
                rate=rate,
                message_id=message_id,
                message_unix=message_unix,
                chat=chat,
                city=city,
            )
        )
    return rows

