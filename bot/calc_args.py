# -*- coding: utf-8 -*-
from __future__ import annotations

from calc_report import parse_calc_cli_argv


def parse_calc_command_args(text: str) -> tuple[float, str, float]:
    """
    /calc RUB usd|eur|cny КУРС  → те же правила, что у CLI ``calc_report.parse_calc_cli_argv``.
    """
    parts = (text or "").strip().split()
    if len(parts) < 4:
        raise ValueError("Формат: /calc RUB usd|eur|cny КУРС")
    head = parts[0]
    if not head.lower().startswith("/calc"):
        raise ValueError("Формат: /calc RUB usd|eur|cny КУРС")
    try:
        return parse_calc_cli_argv(parts[1:4])
    except ValueError as e:
        raise ValueError(str(e)) from e
