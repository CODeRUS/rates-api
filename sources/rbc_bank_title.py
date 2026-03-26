# -*- coding: utf-8 -*-
"""Короткое имя банка из поля ``name`` ответа РБК (кавычки, префиксы АО/ООО)."""
from __future__ import annotations

import re

# Название отделения после маркеров — не использовать как бренд
_BRANCH_MARKERS = (
    " ДО ",
    " ОО №",
    " ОО ",
    " Дополнительный офис ",
)


def rbc_short_bank_name(raw: str) -> str:
    """
    Примеры::

        АО КБ \"ЮНИСТРИМ\" ОО № 193 → ЮНИСТРИМ
        АО \"Реалист Банк\" (бывший ...) ДО \"Центральный\" → Реалист Банк
        ООО КБЭР \"Банк Казани\" ДО \"Таганская\" → Банк Казани
    """
    s = (raw or "").strip()
    if not s:
        return ""

    m = re.search(r'"([^"]+)"', s)
    if m:
        return m.group(1).strip()
    m = re.search(r"«([^»]+)»", s)
    if m:
        return m.group(1).strip()

    cut = s
    for sep in _BRANCH_MARKERS:
        if sep in cut:
            cut = cut.split(sep)[0].strip()

    cut = re.sub(
        r"^(?:(?:АО|ПАО|ООО|АК|НКО)\s+)+",
        "",
        cut,
        flags=re.IGNORECASE,
    ).strip()
    cut = re.sub(r"^(?:КБ|КБЭР|АК)\s+", "", cut, flags=re.IGNORECASE).strip()

    if len(cut) > 72:
        cut = cut[:69] + "…"
    return cut.strip() or s[:72]
