# -*- coding: utf-8 -*-
"""Подгрузка файла ``.env`` из корня репозитория без зависимости python-dotenv."""
from __future__ import annotations

import os
from pathlib import Path

# Для ``rates.py env-status`` — какие ключи показывать (наличие, не значения).
ENV_STATUS_KEYS: tuple[str, ...] = (
    "BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY",
    "BANGKOKBANK_HTTP_TIMEOUT_SEC",
    "BANGKOKBANK_HTTP_MAX_ATTEMPTS",
    "BANGKOKBANK_HTTP_BACKOFF_BASE",
    "UNIRED_TG_PREVIEW_URL",
    "AVOSEND_COOKIE",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_BOT_TOKEN",
    "BOT_ADMIN_ID",
    "BOT_FETCH_TIMEOUT_SEC",
    "RATES_HTTP_MAX_ATTEMPTS",
    "RATES_CACHE_FILE",
    "RATES_USDT_CACHE_FILE",
)


def load_repo_dotenv(repo_root: Path, *, filename: str = ".env") -> bool:
    """
    Прочитать ``repo_root / filename`` и выставить ``os.environ``.
    Уже существующие переменные **не перезаписываются** (как у python-dotenv), чтобы ``export`` в shell имел приоритет.

    :return: ``True``, если файл существовал и был успешно прочитан.
    """
    path = repo_root / filename
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)
    return True
