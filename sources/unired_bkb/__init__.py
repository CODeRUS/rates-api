# -*- coding: utf-8 -*-
"""Unired (userbot cache USD/RUB VISA) × Bangkok Bank TT USD50 (USD/THB) → RUB/THB."""
from __future__ import annotations

import sys
import urllib.error
from typing import List, Optional

import rates_unified_cache as ucc
from rates_categories import SourceCategory

# Атрибуты плагина до импорта rates_sources (избегаем цикла с load_default_sources).
SOURCE_ID = "unired_bkb"
EMOJI = "💱"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

from rates_sources import FetchContext, SourceQuote

from . import bbl_latest_fx as bbl
def help_text() -> str:
    return (
        "Unired (из userbot cache) VISA USD/RUB + Bangkok Bank GetLatestfxrates USD50 TT → RUB/THB.\n"
        "  Нужен env BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY.\n"
        "  Иначе: только справка (без подкоманд)."
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print("\nПример: задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY и проверьте rates.py", file=sys.stderr)
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    rub_per_usd: Optional[float] = None
    thb_per_usd: Optional[float] = None

    doc = ctx.unified_doc if ctx.unified_doc is not None else ucc.load_unified()
    hit = ucc.l1_get_valid(doc, "chatcash:unired_bkb")
    if hit is not None:
        payload = hit[1]
        if isinstance(payload, list):
            for row in payload:
                if not isinstance(row, dict):
                    continue
                if str(row.get("category") or "").strip().lower() != "transfer":
                    continue
                if str(row.get("currency") or "").strip().upper() != "USD":
                    continue
                try:
                    rub_per_usd = float(row.get("rate") or 0.0)
                except (TypeError, ValueError):
                    rub_per_usd = None
                if rub_per_usd and rub_per_usd > 0:
                    break
    if rub_per_usd is None or rub_per_usd <= 0:
        ctx.warnings.append("Unired TG cache: нет свежего USD/RUB в userbot cache")
        return None

    if not bbl.subscription_key_from_env():
        ctx.warnings.append(
            "Bangkok Bank: задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY для курса USD/THB"
        )
    else:
        try:
            thb_per_usd = bbl.fetch_usd50_tt_thb()
        except (RuntimeError, OSError, urllib.error.URLError, ValueError) as e:
            ctx.warnings.append(f"Bangkok Bank: {e}")
        except Exception as e:
            ctx.warnings.append(f"Bangkok Bank: {e}")

    if rub_per_usd is None or rub_per_usd <= 0 or thb_per_usd is None or thb_per_usd <= 0:
        return None

    rub_per_thb = rub_per_usd / thb_per_usd
    if rub_per_thb <= 0:
        return None

    return [
        SourceQuote(
            rub_per_thb,
            "Unired RUB → USD → Bank",
            note="",
        )
    ]
