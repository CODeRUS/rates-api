# -*- coding: utf-8 -*-
"""Unired (Telegram превью USD/RUB VISA) × Bangkok Bank TT USD50 (USD/THB) → RUB/THB."""
from __future__ import annotations

import sys
import urllib.error
from typing import List, Optional

from rates_categories import SourceCategory

# Атрибуты плагина до импорта rates_sources (избегаем цикла с load_default_sources).
SOURCE_ID = "unired_bkb"
EMOJI = "🏧"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

from rates_sources import FetchContext, SourceQuote

from . import bbl_latest_fx as bbl
from . import unired_tg_preview as utg


def help_text() -> str:
    return (
        "Unired @uniredmobile (t.me/s) VISA USD/RUB + Bangkok Bank GetLatestfxrates USD50 TT → RUB/THB.\n"
        "  Нужен env BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY; опционально UNIRED_TG_PREVIEW_URL.\n"
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

    try:
        rub_per_usd = utg.fetch_latest_unired_usd_rub(timeout=25.0)
    except (RuntimeError, OSError, urllib.error.URLError) as e:
        ctx.warnings.append(f"Unired TG: {e}")
    except Exception as e:
        ctx.warnings.append(f"Unired TG: {e}")

    if not bbl.subscription_key_from_env():
        ctx.warnings.append(
            "Bangkok Bank: задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY для курса USD/THB"
        )
    else:
        try:
            thb_per_usd = bbl.fetch_usd50_tt_thb(timeout=35.0)
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
