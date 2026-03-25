# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import rates as rates_mod  # noqa: E402
import usdt_report as usdt_mod  # noqa: E402


def get_summary_text(*, refresh: bool = False) -> str:
    """Та же текстовая сводка, что у ``rates.py`` без ``--json``."""
    parser = rates_mod.build_arg_parser(add_help=False)
    argv = ["--refresh"] if refresh else []
    args = parser.parse_args(argv)
    rows, baseline, warnings = rates_mod.compute_summary_rows(args)
    buf = io.StringIO()
    rates_mod.print_summary_text(rows, baseline, warnings, buf)
    return buf.getvalue()


def get_usdt_text(*, refresh: bool = False) -> str:
    """Текст отчёта USDT (тот же, что ``rates.py usdt``); кеш не зависит от сводки."""
    data, warnings = usdt_mod.compute_usdt_report(refresh=refresh)
    return usdt_mod.format_usdt_report_text(data, warnings)


def split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """Разбить длинный текст (лимит Telegram ~4096, берём запас)."""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
