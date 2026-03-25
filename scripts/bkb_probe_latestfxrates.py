#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверка API Bangkok Bank: последние курсы (GetLatestfxrates).

План интеграции (unired–bkb):
  - Ключ только из окружения: BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY
  - GET …/GetLatestfxrates с браузероподобными заголовками + Accept: application/json
  - В массиве найти объект с Family == USD50, взять TT (THB за 1 USD, TT)
  - При 403/таймаутах попробовать добавить Referer на https://www.bangkokbank.com/

Запуск из корня репозитория::

    export BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY="…"
    python3 scripts/bkb_probe_latestfxrates.py

Опционально (диагностика)::

    python3 scripts/bkb_probe_latestfxrates.py --referer
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import urllib.request

from rates_http import urlopen_retriable

URL = "https://www.bangkokbank.com/api/exchangerateservice/GetLatestfxrates"


def _headers(*, subscription_key: str, with_referer: bool) -> Dict[str, str]:
    h = {
        "accept-language": "en-US,en;q=0.9,ru;q=0.8",
        "ocp-apim-subscription-key": subscription_key,
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
        ),
        "accept": "application/json",
    }
    if with_referer:
        h["referer"] = "https://www.bangkokbank.com/"
    return h


def _as_rate_list(data: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for k in ("data", "rates", "result", "items", "value"):
            v = data.get(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return [x for x in v if isinstance(x, dict)]
    return None


def _parse_tt_usd50(rows: List[Dict[str, Any]]) -> Optional[float]:
    for row in rows:
        fam = row.get("Family") if "Family" in row else row.get("family")
        if fam is None:
            continue
        if str(fam).strip().upper() != "USD50":
            continue
        tt = row.get("TT") if "TT" in row else row.get("tt")
        if tt is None:
            return None
        s = str(tt).strip().replace(",", "").replace(" ", "")
        try:
            return float(s)
        except ValueError:
            return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Проба GetLatestfxrates (Bangkok Bank)")
    ap.add_argument(
        "--referer",
        action="store_true",
        help="Добавить Referer: https://www.bangkokbank.com/",
    )
    ap.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="Таймаут чтения (сек); для CLI по умолчанию 60 (как bbl_latest_fx при отсутствии env)",
    )
    args = ap.parse_args()

    key = (os.environ.get("BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY") or "").strip()
    if not key:
        print(
            "Задайте BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY в окружении.",
            file=sys.stderr,
        )
        return 2

    req = urllib.request.Request(
        URL,
        headers=_headers(subscription_key=key, with_referer=args.referer),
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen_retriable(req, timeout=args.timeout, context=ctx) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    except Exception as e:
        print(f"HTTP/сеть: {e}", file=sys.stderr)
        return 1

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Не JSON: {e}\n{text_preview(raw)}", file=sys.stderr)
        return 1

    rows = _as_rate_list(data)
    if not rows:
        print("Не удалось извлечь список котировок из ответа.", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False, indent=2)[:4000])
        return 1

    tt = _parse_tt_usd50(rows)
    print(f"Записей: {len(rows)}")
    if tt is not None:
        print(f"USD50 TT (THB за 1 USD): {tt}")
    else:
        print("Объект Family=USD50 или поле TT не найдены.", file=sys.stderr)
        print(json.dumps(rows[:3], ensure_ascii=False, indent=2))
        return 1
    return 0


def text_preview(s: str, n: int = 400) -> str:
    s = s.strip()
    return s if len(s) <= n else s[:n] + "…"


if __name__ == "__main__":
    raise SystemExit(main())
