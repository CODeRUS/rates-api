# -*- coding: utf-8 -*-
"""
Публичная веб-лента канала Telegram (``/s/…``): курс «Россиядан - VISA» USD/RUB.

Формат в посте: ``1 $ = 87,82 RUB`` (в HTML часто ``&#036;`` вместо ``$``).
Берётся **последний** по странице блок с ``Россиядан`` и ``VISA``, где есть строка курса.
"""
from __future__ import annotations

import html as html_module
import os
import re
import ssl
import urllib.error
import urllib.request
from typing import Optional

from rates_http import urlopen_retriable

DEFAULT_PREVIEW_URL = "https://t.me/s/uniredmobile"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# После очистки HTML: RUB за 1 USD (как в посте «1 $ = 81,98 RUB»)
_RE_USD_RUB = re.compile(
    r"1\s*\$\s*=\s*([0-9]+(?:[.,\s\u00a0][0-9]{1,3})*(?:[.,][0-9]+)?)\s*RUB",
    re.IGNORECASE,
)


def preview_url_from_env() -> str:
    return (os.environ.get("UNIRED_TG_PREVIEW_URL") or DEFAULT_PREVIEW_URL).strip() or DEFAULT_PREVIEW_URL


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def _normalize_money_num(s: str) -> Optional[float]:
    t = html_module.unescape(s.strip())
    t = t.replace("\u00a0", " ").replace(" ", "")
    t = t.replace(",", ".")
    if t.count(".") > 1:
        t = t.replace(".", "", t.count(".") - 1)
    try:
        v = float(t)
    except ValueError:
        return None
    return v if v > 0 else None


def extract_latest_usd_rub_from_html(page_html: str) -> Optional[float]:
    """RUB за 1 USD по последнему подходящему посту на странице."""
    parts = re.split(
        r'<div class="tgme_widget_message_text js-message_text"',
        page_html,
        flags=re.I,
    )
    if len(parts) < 2:
        return None

    for chunk in reversed(parts[1:]):
        plain = _strip_tags(chunk)
        plain_u = html_module.unescape(plain)
        if "Россиядан" not in plain_u and "россиядан" not in plain_u.lower():
            continue
        if "VISA" not in plain_u.upper():
            continue
        m = _RE_USD_RUB.search(plain_u.replace("&#036;", "$"))
        if not m:
            m = _RE_USD_RUB.search(plain_u)
        if not m:
            continue
        return _normalize_money_num(m.group(1))
    return None


def fetch_channel_preview_html(*, url: Optional[str] = None, timeout: float = 25.0) -> str:
    u = (url or preview_url_from_env()).strip()
    req = urllib.request.Request(
        u,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    ctx = ssl.create_default_context()
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def fetch_latest_unired_usd_rub(
    *,
    preview_url: Optional[str] = None,
    timeout: float = 25.0,
) -> float:
    html = fetch_channel_preview_html(url=preview_url, timeout=timeout)
    rub = extract_latest_usd_rub_from_html(html)
    if rub is None:
        raise RuntimeError("Unired: нет блока Россиядан+VISA с «1 $ = … RUB» на странице превью")
    return rub
