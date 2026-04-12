# -*- coding: utf-8 -*-
"""Форматирование полей для пошаговых логов (обрезка длинных строк)."""
from __future__ import annotations

import json
from typing import List


def clip_text(text: str, max_len: int) -> str:
    if max_len <= 0 or len(text) <= max_len:
        return text
    return (
        text[:max_len]
        + f"\n… [обрезано для лога, было символов: {len(text)}]"
    )


def messages_for_log(
    messages: List[dict[str, str]],
    *,
    max_total: int,
) -> str:
    raw = json.dumps(messages, ensure_ascii=False, indent=2)
    return clip_text(raw, max_total)
