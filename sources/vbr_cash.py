# -*- coding: utf-8 -*-
"""Курсы наличной продажи с Выберу.ру (vbr.ru), API bank-doubled-rates-table (HTML)."""
from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USER_AGENT = "rates-vbr-cash/1.0 (python)"

# Ключ — как в sources.banki_cash.BANKI_REGIONS / cash_report._CASH_LOCATIONS.
# Поддомен vbr без ".vbr.ru"; СПб — отдельный host www + гео.
_VBR_KIND_SUB = "subdomain"
_VBR_KIND_WWW_GEO = "www_geo"

VBR_ENDPOINTS: Dict[str, Dict[str, object]] = {
    "moskva": {"kind": _VBR_KIND_SUB, "host": "moskva"},
    "sankt-peterburg": {
        "kind": _VBR_KIND_WWW_GEO,
        "lat": 59.9222015,
        "lon": 30.3398645,
    },
    "kazan": {"kind": _VBR_KIND_SUB, "host": "kazan"},
    "rostov-na-donu": {"kind": _VBR_KIND_SUB, "host": "rostov-na-donu"},
    "novosibirsk": {"kind": _VBR_KIND_SUB, "host": "novosibirsk"},
    "krasnoyarsk": {"kind": _VBR_KIND_SUB, "host": "krasnojarsk"},
    "irkutsk": {"kind": _VBR_KIND_SUB, "host": "irkutsk"},
    "ekaterinburg": {"kind": _VBR_KIND_SUB, "host": "ekaterinburg"},
}


def build_vbr_rates_url(banki_region_key: str, currency1: str) -> Optional[str]:
    """
    Полный URL таблицы курсов для региона (ключ Banki) и валюты (USD, EUR, CNY).

    Везде ``sortType=1&sortDirection=0``; для СПб — ``www.vbr.ru`` и координаты.
    """
    cfg = VBR_ENDPOINTS.get(banki_region_key)
    if cfg is None:
        return None
    kind = str(cfg.get("kind") or "")
    if kind == _VBR_KIND_SUB:
        netloc = f'{cfg["host"]}.vbr.ru'
        geo = "locationNearby=false&latitude=0&longitude=0"
    elif kind == _VBR_KIND_WWW_GEO:
        netloc = "www.vbr.ru"
        geo = (
            "locationNearby=true"
            f'&latitude={cfg["lat"]}&longitude={cfg["lon"]}'
        )
    else:
        return None
    q = (
        f"currency1={currency1}&currency2=&sortType=1&sortDirection=0&currencyForSorting=1&"
        f"page=1&pageSize=500&topBanks=0&bankIds=&showFirstCurrency=true&showSecondCurrency=false&"
        f"{geo}&showDates=true&withOffices=false&excludeBankId="
    )
    return f"https://{netloc}/api/currency/bank-doubled-rates-table/?{q}"


def fetch_vbr_rates_html(
    banki_region_key: str,
    currency1: str,
    *,
    timeout: float,
) -> Optional[str]:
    url = build_vbr_rates_url(banki_region_key, currency1)
    if not url:
        return None
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
        logger.info("vbr_cash fetch failed %s %s: %s", banki_region_key, currency1, e)
        return None
    if not raw:
        return None
    return raw.decode(charset, errors="replace")


_ROW_ANCHOR = 'name="RatesTableExpand"'


def _parse_rub_amount(text: str) -> Optional[float]:
    t = text.replace("\xa0", " ").replace("₽", "").strip()
    t = re.sub(r"\s+", "", t)
    t = t.replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)", t)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    return v if v > 0 else None


def _first_rate_cell_inner(row_html: str, currency: str) -> Optional[str]:
    """Первое ``td.rates-val`` с ``data-col=currency`` — внутренность ``td``."""
    cur = re.escape(currency)
    patterns = (
        rf'<td[^>]*class="[^"]*rates-val[^"]*"[^>]*data-col="{cur}"[^>]*>(.*?)</td>',
        rf'<td[^>]*data-col="{cur}"[^>]*class="[^"]*rates-val[^"]*"[^>]*>(.*?)</td>',
    )
    for pat in patterns:
        m = re.search(pat, row_html, flags=re.IGNORECASE | re.DOTALL)
        if m:
            return m.group(1)
    return None


def _cell_to_sell(inner_td: str) -> Optional[float]:
    m = re.search(
        r'<div[^>]*class="[^"]*rates-calc-block[^"]*"[^>]*>\s*([^<]+)',
        inner_td,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    return _parse_rub_amount(m.group(1))


def _bank_display_from_row(row_html: str) -> str:
    m = re.search(
        r'<span[^>]*class="[^"]*rates-name-bank[^"]*"[^>]*>\s*([^<]+)',
        row_html,
        flags=re.IGNORECASE,
    )
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()
    m = re.search(r'<img[^>]*alt="([^"]*)"', row_html, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return "—"


def vbr_sell_rows(html: str, currency: str) -> List[Tuple[float, str]]:
    """
    Список (курс из **первой** колонки ``rates-val`` для ``currency``, банк) по HTML ответа API.

    Берутся строки ``<tr … name="RatesTableExpand" …>``.
    """
    if not html or not html.strip():
        return []
    cur_upper = currency.strip().upper()
    out: List[Tuple[float, str]] = []
    pos = 0
    while True:
        anchor = html.find(_ROW_ANCHOR, pos)
        if anchor < 0:
            break
        tr_open = html.rfind("<tr", pos, anchor)
        if tr_open < 0:
            pos = anchor + len(_ROW_ANCHOR)
            continue
        tr_close = html.find("</tr>", anchor)
        if tr_close < 0:
            break
        tr_close += len("</tr>")
        row_html = html[tr_open:tr_close]
        pos = tr_close

        inner = _first_rate_cell_inner(row_html, cur_upper)
        if not inner:
            continue
        sell = _cell_to_sell(inner)
        if sell is None:
            continue
        label = _bank_display_from_row(row_html)
        out.append((sell, label))
    return out
