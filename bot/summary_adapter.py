# -*- coding: utf-8 -*-

import io
import logging
import sys
from pathlib import Path
from typing import List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import cash_report as cash_mod  # noqa: E402
import exchange_report as exchange_mod  # noqa: E402
import rates as rates_mod  # noqa: E402
import usdt_report as usdt_mod  # noqa: E402
from sources.rshb_unionpay.card_fx_calculator import build_rshb_text  # noqa: E402

logger = logging.getLogger(__name__)


def get_summary_text(
    *,
    refresh: bool = False,
    unified_allow_stale: bool = True,
    output_filter: str = "",
) -> str:
    """Та же текстовая сводка, что у ``rates.py`` без ``--json``."""
    parser = rates_mod.build_arg_parser(add_help=False)
    argv: list[str] = []
    if refresh:
        argv.append("--refresh")
    of = (output_filter or "").strip()
    if of:
        argv.extend(["--filter", of])
    args = parser.parse_args(argv)
    if not refresh:
        args.unified_allow_stale = bool(unified_allow_stale)
    rows, baseline, warnings = rates_mod.compute_summary_rows(args)
    rows = rates_mod._maybe_apply_output_filter(args, rows)
    buf = io.StringIO()
    rates_mod.print_summary_text(rows, baseline, warnings, buf)
    get_summary_text._needs_background_refresh = bool(
        getattr(args, "_unified_served_stale_l2", False)
        and (not refresh)
        and unified_allow_stale
    )
    return buf.getvalue()


get_summary_text._needs_background_refresh = False  # type: ignore[attr-defined]


def get_cash_text(
    *,
    refresh: bool = False,
    top_n: int = 3,
    unified_allow_stale: bool = True,
    city_label: str = "",
) -> str:
    """Тот же текст, что ``rates.py cash``."""
    allow = (not refresh) and unified_allow_stale
    text = cash_mod.format_cash_report_with_warnings(
        top_n=top_n,
        refresh=refresh,
        unified_allow_stale=allow,
        city_label=(city_label or "").strip() or None,
    )
    get_cash_text._needs_background_refresh = bool(
        (not refresh) and allow and cash_mod._unified_served_stale_l2_plain
    )
    return text


get_cash_text._needs_background_refresh = False  # type: ignore[attr-defined]


def get_cash_cities_text(*, use_banki: bool = True) -> str:
    locs = cash_mod._locations(use_banki)
    lines = ["Доступные города:"]
    for i, x in enumerate(locs, start=1):
        lines.append(f"{i}. {x[0]}")
    return "\n".join(lines) + "\n"


def get_exchange_text(
    *,
    refresh: bool = False,
    top_n: int = 10,
    lang: str = "ru",
    unified_allow_stale: bool = True,
) -> str:
    """Тот же текст, что ``rates.py exchange``."""
    allow = (not refresh) and unified_allow_stale
    text = exchange_mod.format_exchange_report_with_warnings(
        top_n=top_n,
        lang=lang,
        refresh=refresh,
        unified_allow_stale=allow,
    )
    get_exchange_text._needs_background_refresh = bool(
        (not refresh) and allow and exchange_mod._unified_served_stale_l2
    )
    return text


get_exchange_text._needs_background_refresh = False  # type: ignore[attr-defined]


def get_usdt_text(
    *, refresh: bool = False, unified_allow_stale: bool = True
) -> str:
    """Текст отчёта USDT (тот же, что ``rates.py usdt``)."""
    allow = (not refresh) and unified_allow_stale
    data, warnings = usdt_mod.compute_usdt_report(
        refresh=refresh, unified_allow_stale=allow
    )
    get_usdt_text._needs_background_refresh = bool(
        (not refresh) and allow and usdt_mod._unified_served_stale_l2
    )
    return usdt_mod.format_usdt_report_text(data, warnings)


get_usdt_text._needs_background_refresh = False  # type: ignore[attr-defined]


def get_rshb_text(
    *, thb_nets: Optional[List[float]] = None, atm_fee: float = 250.0
) -> str:
    """Текст отчёта THB/RUB для РСХБ UnionPay; несколько сумм — несколько блоков снятия."""
    nets = thb_nets if thb_nets is not None else [30_000.0]
    return build_rshb_text(thb_nets=nets, atm_fee_thb=atm_fee)


def run_background_unified_refresh(kind: str) -> None:
    """Синхронное обновление кеша после ответа из stale L2 (вызывать из to_thread)."""
    try:
        if kind == "summary":
            get_summary_text(refresh=False, unified_allow_stale=False)
        elif kind == "usdt":
            get_usdt_text(refresh=False, unified_allow_stale=False)
        elif kind == "cash":
            get_cash_text(refresh=False, unified_allow_stale=False)
        elif kind == "exchange":
            get_exchange_text(refresh=False, unified_allow_stale=False)
        else:
            logger.warning("unknown background refresh kind: %s", kind)
    except Exception:
        logger.exception("background unified refresh failed (%s)", kind)


def split_for_telegram(text: str, limit: int = 4000) -> list[str]:
    """Разбить длинный текст (лимит Telegram ~4096, берём запас)."""
    if not text:
        return [""]
    if len(text) <= limit:
        return [text]
    return [text[i : i + limit] for i in range(0, len(text), limit)]
