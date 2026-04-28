# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import List, Optional, TYPE_CHECKING

import rates_unified_cache as ucc
from rates_categories import SourceCategory

if TYPE_CHECKING:
    from rates_sources import FetchContext, SourceQuote

SOURCE_ID = "sberbank_qr"
EMOJI = "📲"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_KEY = "prim:sber_qr_transfer"


def help_text() -> str:
    return "Сбербанк QR (transfer): курс берется только из unified cache, обновляется cron."


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def summary(ctx: "FetchContext") -> Optional[List["SourceQuote"]]:
    from rates_sources import SourceQuote

    doc = ctx.unified_doc if ctx.unified_doc is not None else ucc.load_unified()
    hit = ucc.prim_get_valid(doc, _KEY)
    if hit is None:
        hit = ucc.l1_get_valid(doc, _KEY)
    if hit is None:
        ctx.warnings.append("sberbank_qr: нет записи prim:sber_qr_transfer")
        return None
    payload = hit[1]
    if not isinstance(payload, dict):
        ctx.warnings.append("sberbank_qr: payload не dict")
        return None
    try:
        rate = float(payload.get("rate") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    if rate <= 0:
        ctx.warnings.append("sberbank_qr: rate отсутствует или <= 0")
        return None
    note = str(payload.get("note") or "").strip()
    return [SourceQuote(rate=rate, label="Сбербанк QR", note=note)]
