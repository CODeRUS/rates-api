# -*- coding: utf-8 -*-
"""Avosend (RUB → USD на карту) × Bangkok Bank TT USD50 → RUB/THB в сводке."""
from __future__ import annotations

import urllib.error
from typing import TYPE_CHECKING, List, Optional

from rates_categories import SourceCategory

SOURCE_ID = "avosend_bkb"
EMOJI = "💳"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

if TYPE_CHECKING:
    from rates_sources import FetchContext, SourceQuote


def help_text() -> str:
    return (
        "Avosend (карта USD) × Bangkok Bank GetLatestfxrates USD50 TT → RUB/THB.\n"
        "  Нужны BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY; для Avosend опционально AVOSEND_COOKIE.\n"
        "  Курс: usd = (avosend_rub - fee) * convertRate, затем THB как у unired_bkb (TT USD50)."
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        return 0
    print(help_text())
    return 0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from rates_sources import SourceQuote, fmt_money_ru
    from sources.avosend import avosend_commission as av
    from sources.unired_bkb import bbl_latest_fx as bbl

    from .calc import fee_and_convert_rate, rub_per_thb

    rub = float(ctx.avosend_rub)
    if rub <= 0:
        return None

    note = f"от {fmt_money_ru(rub)} RUB"

    if not bbl.subscription_key_from_env():
        ctx.warnings.append(
            "Avosend×BBL: задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY для USD/THB"
        )
        return None

    try:
        data = av.fetch_commission(rub, av.TransferMode.CARD)
    except (RuntimeError, OSError, urllib.error.URLError, ValueError) as e:
        ctx.warnings.append(f"Avosend×BBL Avosend: {e}")
        return None
    except Exception as e:
        ctx.warnings.append(f"Avosend×BBL Avosend: {e}")
        return None

    fee, convert_rate = fee_and_convert_rate(data)
    if fee is None:
        ctx.warnings.append("Avosend×BBL: в ответе нет корректного fee")
        return None
    if convert_rate is None:
        ctx.warnings.append("Avosend×BBL: в ответе нет корректного convertRate")
        return None

    try:
        thb_per_usd = bbl.fetch_usd50_tt_thb(timeout=35.0)
    except (RuntimeError, OSError, urllib.error.URLError, ValueError) as e:
        ctx.warnings.append(f"Avosend×BBL Bangkok Bank: {e}")
        return None
    except Exception as e:
        ctx.warnings.append(f"Avosend×BBL Bangkok Bank: {e}")
        return None

    rate = rub_per_thb(rub, fee, convert_rate, thb_per_usd)
    if rate is None:
        ctx.warnings.append(
            "Avosend×BBL: не удалось посчитать курс (комиссия/курс или нулевой объём THB)"
        )
        return None

    return [
        SourceQuote(
            rate,
            "Avosend RUB → карта USD → Bank",
            note=note,
        )
    ]
