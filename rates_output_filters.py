# -*- coding: utf-8 -*-
"""Именованные пресеты постфильтрации строк текстовой сводки (после сборки, до печати)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Pattern, Sequence, Tuple

from rates_sources import RateRow

__all__ = [
    "PRESET_NAMES",
    "apply_summary_row_filter",
    "apply_summary_warning_filter",
]


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
    "travelask": _OutputFilterPreset(
        substrings=(
            "IT Обмен",
            "Fly Currency",
            "Korona",
            "Avosend",
            "Bybit",
            "Unired",
            "Ex24",
            "Novawallet",
            "Payscan",
            "Мультитрансфер",
            "Bereza",
            "Exasia",
            "XAACTA",
            "Senate",
            "Moreta",
            "KwikPay",
        ),
    ),
}

# Алиасы для удобства (CLI/bot): исторические id + короткое имя "ta".
_PRESETS_RAW["-1001835014897"] = _PRESETS_RAW["travelask"]
_PRESETS_RAW["ta"] = _PRESETS_RAW["travelask"]


_PRESET_REGEX_COMPILED: Dict[str, Tuple[Pattern[str], ...]] = {
    k: _compile_regexes(v.regexes) for k, v in _PRESETS_RAW.items()
}


PRESET_NAMES: Tuple[str, ...] = tuple(sorted(_PRESETS_RAW.keys()))


def _row_haystack(row: RateRow) -> str:
    parts = [row.label or "", row.note or ""]
    return " ".join(parts)


def _preset_for(filter_name: Optional[str]) -> Optional[Tuple[_OutputFilterPreset, Tuple[Pattern[str], ...]]]:
    name = (filter_name or "").strip().lower()
    if not name:
        return None
    preset = _PRESETS_RAW.get(name)
    if preset is None:
        return None
    return preset, _PRESET_REGEX_COMPILED[name]


def _text_matches_preset(text: str, preset: _OutputFilterPreset, rx: Tuple[Pattern[str], ...]) -> bool:
    hay = text.casefold()
    for s in preset.substrings:
        if s and s.casefold() in hay:
            return True
    for pat in rx:
        if pat.search(text):
            return True
    return False


def apply_summary_row_filter(
    rows: Sequence[RateRow], filter_name: Optional[str]
) -> List[RateRow]:
    """
    Убирает строки по пресету (подстроки и regex). Forex (``is_baseline``) не трогаем.
    Неизвестное имя пресета — без изменений, без предупреждений.
    """
    found = _preset_for(filter_name)
    if found is None:
        return list(rows)
    preset, rx = found
    out: List[RateRow] = []
    for row in rows:
        if row.is_baseline:
            out.append(row)
            continue
        if not _text_matches_preset(_row_haystack(row), preset, rx):
            out.append(row)
    return out


def apply_summary_warning_filter(
    warnings: Sequence[str], filter_name: Optional[str]
) -> List[str]:
    """Те же подстроки, что у строк сводки. Неизвестный пресет — список без изменений."""
    found = _preset_for(filter_name)
    if found is None:
        return list(warnings)
    preset, rx = found
    return [w for w in warnings if not _text_matches_preset(w, preset, rx)]
