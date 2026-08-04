# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from rates_http import urlopen_retriable
from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "bereza"
EMOJI = "🤑"
IS_BASELINE = False
CATEGORY = SourceCategory.EXCHANGER

_SITE = "https://bereza-exchange.com"
_CONVERT_URL = f"{_SITE}/api/convert"
_ACCESS_TOKEN_URL = f"{_SITE}/api/access-token"
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36"
)

# Короткоживущий токен из /api/access-token (кэш процесса).
_token_cache: Tuple[str, float] = ("", 0.0)  # token, expires_unix


def help_text() -> str:
    return (
        "Bereza RUB→THB: transfer (SBP) и cash (CASH) через bereza-exchange.com "
        "(/api/access-token + /api/convert)."
    )


def command(argv: list[str]) -> int:
    print(help_text())
    return 0


def _browser_headers(*, access_token: Optional[str] = None) -> Dict[str, str]:
    hdr = {
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": _UA,
        "Referer": f"{_SITE}/",
        "Origin": _SITE,
        "sec-ch-ua": '"Not:A-Brand";v="99", "Chromium";v="112"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Linux"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    }
    if access_token:
        hdr["x-bereza-access-token"] = access_token
    return hdr


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


def _http_json(url: str, *, headers: Dict[str, str], timeout: float) -> Any:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urlopen_retriable(
            req, timeout=timeout, context=ssl.create_default_context()
        ) as resp:
            raw = resp.read().decode(
                resp.headers.get_content_charset() or "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as e:
        detail = e.read()[:500].decode("utf-8", errors="replace")
        raise RuntimeError(f"Bereza HTTP {e.code}: {detail}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Bereza: не JSON в ответе {raw[:120]!r}") from e


def fetch_access_token(*, timeout: float = 20.0, force: bool = False) -> str:
    """GET /api/access-token → краткоживущий X-Bereza-Access-Token."""
    global _token_cache
    tok, exp = _token_cache
    now = time.time()
    if not force and tok and exp > now + 30:
        return tok
    data = _http_json(
        _ACCESS_TOKEN_URL,
        headers=_browser_headers(),
        timeout=timeout,
    )
    if not isinstance(data, dict):
        raise RuntimeError(f"Bereza access-token: неожиданный ответ {type(data).__name__}")
    token = str(data.get("token") or "").strip()
    if not token:
        raise RuntimeError(f"Bereza access-token: нет token в ответе {data!r}")
    expires = data.get("expires")
    try:
        exp_unix = float(expires) if expires is not None else now + 300.0
    except (TypeError, ValueError):
        exp_unix = now + 300.0
    _token_cache = (token, exp_unix)
    return token


def _convert_rub_to_thb_pair(
    amount_rub: float,
    from_currency: str,
    *,
    timeout: float = 20.0,
    access_token: Optional[str] = None,
) -> tuple[float, float]:
    """Возвращает (₽ за 1 THB, получено THB) для переданной суммы RUB."""
    token = access_token or fetch_access_token(timeout=timeout)
    qs = urllib.parse.urlencode(
        {
            "amount": int(round(amount_rub)),
            "from_currency": from_currency,
            "to_currency": "THB",
        }
    )
    url = f"{_CONVERT_URL}?{qs}"

    def _once(tok: str) -> Any:
        return _http_json(
            url,
            headers=_browser_headers(access_token=tok),
            timeout=timeout,
        )

    try:
        data = _once(token)
    except RuntimeError as e:
        # Токен мог протухнуть — один повтор с force refresh.
        if "401" in str(e) or "403" in str(e) or "access token" in str(e).lower():
            token = fetch_access_token(timeout=timeout, force=True)
            data = _once(token)
        else:
            raise
    thb = _extract_to_amount(data)
    if thb is None or thb <= 0:
        raise RuntimeError(f"Bereza: не удалось извлечь THB из ответа {str(data)[:160]!r}")
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
                category=SourceCategory.EXCHANGER,
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
