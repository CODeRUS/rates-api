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


def _trim_before_first_comma_or_period(s: str) -> str:
    """Обрезка до первой ``,`` или ``.`` (адрес после названия без кавычек)."""
    for i, ch in enumerate(s):
        if ch in ",.":
            head = s[:i].strip()
            return head if head else s
    return s


def rbc_short_bank_name(raw: str) -> str:
    """
    Примеры::

        АО КБ \"ЮНИСТРИМ\" ОО № 193 → ЮНИСТРИМ
        АО \"Реалист Банк\" (бывший ...) ДО \"Центральный\" → Реалист Банк
        ООО КБЭР \"Банк Казани\" ДО \"Таганская\" → Банк Казани
        Без кавычек: всё после первой ``,`` или ``.`` отбрасывается (адрес).
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

    cut = _trim_before_first_comma_or_period(s)
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


def canonical_bank_key(raw: str) -> str:
    """
    Ключ для сопоставления одного бренда между РБК (длинные названия офисов)
    и Banki.ru (короткое имя банка): ``rbc_short_bank_name`` + пробелы + регистр.
    """
    short = rbc_short_bank_name(raw) or (raw or "")
    s = " ".join(short.split()).casefold().replace("ё", "е")
    return s
