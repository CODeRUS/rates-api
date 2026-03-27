# -*- coding: utf-8 -*-
"""Именованные пресеты постфильтрации строк текстовой сводки (после сборки, до печати)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

from rates_sources import RateRow

__all__ = ["PRESET_NAMES", "apply_summary_row_filter"]


@dataclass(frozen=True)
class _OutputFilterPreset:
    substrings: Tuple[str, ...] = ()
    regexes: Tuple[str, ...] = ()


def _compile_regexes(patterns: Sequence[str]) -> Tuple[Pattern[str], ...]:
    out: List[Pattern[str]] = []
    for p in patterns:
        out.append(re.compile(p))
    return tuple(out)


_PRESETS_RAW: Dict[str, _OutputFilterPreset] = {
    "161665026": _OutputFilterPreset(
        substrings=(
            "IT Обмен",
            "Fly Currency",
            "Korona",
            "Avosend RUB",
            "Bybit",
            "Unired",
            "Ex24",
        ),
    ),
}

# Алиасы для удобства (CLI/bot): исторические id + короткое имя "ta".
_PRESETS_RAW["travelask"] = _PRESETS_RAW["161665026"]
_PRESETS_RAW["ta"] = _PRESETS_RAW["161665026"]


_PRESET_REGEX_COMPILED: Dict[str, Tuple[Pattern[str], ...]] = {
    k: _compile_regexes(v.regexes) for k, v in _PRESETS_RAW.items()
}


PRESET_NAMES: Tuple[str, ...] = tuple(sorted(_PRESETS_RAW.keys()))


def _row_haystack(row: RateRow) -> str:
    parts = [row.label or "", row.note or ""]
    return " ".join(parts)


def apply_summary_row_filter(
    rows: Sequence[RateRow], filter_name: Optional[str]
) -> List[RateRow]:
    """
    Убирает строки по пресету (подстроки и regex). Forex (``is_baseline``) не трогаем.
    Неизвестное имя пресета — без изменений, без предупреждений.
    """
    name = (filter_name or "").strip().lower()
    if not name:
        return list(rows)
    preset = _PRESETS_RAW.get(name)
    if preset is None:
        return list(rows)
    rx = _PRESET_REGEX_COMPILED[name]
    subs = preset.substrings
    out: List[RateRow] = []
    for row in rows:
        if row.is_baseline:
            out.append(row)
            continue
        text = _row_haystack(row)
        hay = text.casefold()
        drop = False
        for s in subs:
            if s and s.casefold() in hay:
                drop = True
                break
        if not drop:
            for pat in rx:
                if pat.search(text):
                    drop = True
                    break
        if not drop:
            out.append(row)
    return out
