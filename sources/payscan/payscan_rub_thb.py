# -*- coding: utf-8 -*-
"""Payscan zap_ok: курс RUB за 1 THB (plain text)."""
from __future__ import annotations

import os
import re
import ssl
import sys
import urllib.request
from typing import Optional

from rates_http import urlopen_retriable

DEFAULT_URL = "https://payscan.ru/zap_ok_rate.php?d=THB"
USER_AGENT = "rates-api/payscan/1.0 (python)"


def payscan_url() -> str:
    raw = (os.environ.get("PAYSCAN_THB_URL") or "").strip()
    return raw or DEFAULT_URL


def fetch_rub_per_thb(*, timeout: float = 20.0, url: Optional[str] = None) -> float:
    u = url or payscan_url()
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        u,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
    )
    with urlopen_retriable(req, timeout=timeout, context=ctx) as r:
        raw_bytes = r.read()
    text = raw_bytes.decode("utf-8", errors="replace").strip()
    # Иногда обёртка или пробелы — берём первое похожее на число
    if not text:
        raise RuntimeError("Payscan: пустой ответ")
    m = re.search(r"[-+]?\d+[.,]?\d*", text.replace("\xa0", " "))
    if not m:
        raise RuntimeError(f"Payscan: нет числа в ответе: {text[:80]!r}")
    s = m.group(0).replace(",", ".")
    try:
        v = float(s)
    except ValueError as e:
        raise RuntimeError(f"Payscan: не удалось разобрать число {s!r}") from e
    if not (0.05 < v < 150.0):
        raise RuntimeError(f"Payscan: подозрительное значение {v} (ожидали RUB/THB)")
    return v


def cli_main(argv: Optional[list[str]] = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Payscan THB→RUB (zap_ok_rate)")
    p.add_argument(
        "--url",
        default=None,
        help=f"URL (по умолчанию {DEFAULT_URL} или PAYSCAN_THB_URL)",
    )
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args(argv)
    r = fetch_rub_per_thb(timeout=args.timeout, url=args.url)
    print(f"{r:.4f} RUB за 1 THB")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
