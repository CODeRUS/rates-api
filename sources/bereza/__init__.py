# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "bereza"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

_BASE_URL = "https://exchangerates-production.up.railway.app/api/convert"
_UA = "rates-api/bereza/1.0 (python)"


def help_text() -> str:
    return "Bereza RUB→THB: transfer (SBP) и cash (CASH) из exchangerates API."


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _extract_to_amount(data: Any) -> Optional[float]:
    if isinstance(data, (int, float)):
        v = float(data)
        return v if v > 0 else None
    if not isinstance(data, dict):
        return None
    candidates = (
        "to_amount",
        "toAmount",
        "result",
        "converted_amount",
        "convertedAmount",
        "value",
        "amount_to",
    )
    for k in candidates:
        v = data.get(k)
        if isinstance(v, (int, float)) and float(v) > 0:
            return float(v)
        if isinstance(v, str):
            try:
                f = float(v.replace(",", ".").strip())
            except ValueError:
                continue
            if f > 0:
                return f
    nested = data.get("data")
    if nested is not None:
        return _extract_to_amount(nested)
    return None


def _convert_rub_to_thb_pair(
    amount_rub: float,
    from_currency: str,
    *,
    timeout: float = 20.0,
) -> tuple[float, float]:
    """Возвращает (₽ за 1 THB, получено THB) для переданной суммы RUB."""
    qs = urllib.parse.urlencode(
        {
            "amount": int(round(amount_rub)),
            "from_currency": from_currency,
            "to_currency": "THB",
        }
    )
    url = f"{_BASE_URL}?{qs}"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": _UA,
        },
    )
    with urlopen_retriable(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    data = json.loads(raw)
    thb = _extract_to_amount(data)
    if thb is None or thb <= 0:
        raise RuntimeError(f"Bereza: не удалось извлечь THB из ответа {raw[:120]!r}")
    rub_per_thb = float(amount_rub) / thb
    if rub_per_thb <= 0:
        raise RuntimeError("Bereza: получен невалидный курс RUB/THB")
    return rub_per_thb, thb


def _convert_rub_to_thb(
    amount_rub: float,
    from_currency: str,
    *,
    timeout: float = 20.0,
) -> float:
    rub_per_thb, _ = _convert_rub_to_thb_pair(amount_rub, from_currency, timeout=timeout)
    return rub_per_thb


_DEFAULT_TRANSFER_RUB = 30_000.0
_DEFAULT_CASH_RUB = 10_000.0
_MIN_SCENARIO_RUB = 1_000.0


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    transfer_rub = _DEFAULT_TRANSFER_RUB
    cash_rub = _DEFAULT_CASH_RUB
    target_thb = (
        float(ctx.receiving_thb)
        if (ctx.receiving_thb is not None and float(ctx.receiving_thb) > 0)
        else None
    )
    if target_thb is not None:
        try:
            _, probe_thb = _convert_rub_to_thb_pair(transfer_rub, "RUB (SBP)")
            if probe_thb > 0:
                scale = target_thb / probe_thb
                transfer_rub = max(_MIN_SCENARIO_RUB, _DEFAULT_TRANSFER_RUB * scale)
                cash_rub = max(_MIN_SCENARIO_RUB, _DEFAULT_CASH_RUB * scale)
        except Exception as e:
            ctx.warnings.append(
                f"Bereza: не удалось подогнать суммы под receiving_thb={target_thb:g}: {e}"
            )

    out: List[SourceQuote] = []
    try:
        tr = _convert_rub_to_thb(transfer_rub, "RUB (SBP)")
        out.append(
            SourceQuote(
                tr,
                "Bereza СБП",
                note=f"≈ {fmt_money_ru(transfer_rub)} RUB",
                category=SourceCategory.TRANSFER,
                emoji=EMOJI,
            )
        )
    except Exception as e:
        ctx.warnings.append(f"Bereza transfer: {e}")
    try:
        cash = _convert_rub_to_thb(cash_rub, "RUB (CASH)")
        out.append(
            SourceQuote(
                cash,
                "Bereza Наличные",
                note=f"≈ {fmt_money_ru(cash_rub)} RUB",
                category=SourceCategory.CASH_RUB,
                emoji="•",
            )
        )
    except Exception as e:
        ctx.warnings.append(f"Bereza cash: {e}")
    return out or None
