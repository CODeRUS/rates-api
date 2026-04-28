# -*- coding: utf-8 -*-
"""Подгрузка файла ``.env`` из корня репозитория без зависимости python-dotenv."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping

# Для ``rates.py env-status`` — какие ключи показывать (наличие, не значения).
ENV_STATUS_KEYS: tuple[str, ...] = (
    "BANGKOKBANK_OCP_APIM_SUBSCRIPTION_KEY",
    "BANGKOKBANK_HTTP_TIMEOUT_SEC",
    "BANGKOKBANK_HTTP_MAX_ATTEMPTS",
    "BANGKOKBANK_HTTP_BACKOFF_BASE",
    "BANGKOKBANK_HTTP_CLIENT",
    "BANGKOKBANK_CURL_IMPERSONATE",
    "UNIRED_TG_PREVIEW_URL",
    "AVOSEND_COOKIE",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_BOT_TOKEN",
    "BOT_ADMIN_ID",
    "BOT_FETCH_TIMEOUT_SEC",
    "RATES_HTTP_MAX_ATTEMPTS",
    "RATES_DISABLE_RBC",
    "RATES_DISABLE_BANKI",
    "RATES_DISABLE_VBR",
    "SBER_QR_HOSTNAME",
    "SBER_QR_UFS_TOKEN",
    "SBER_QR_UFS_SESSION",
    "SBER_QR_LINK",
    "SBER_QR_TIMEOUT_SEC",
    "SBER_QR_VERIFY_SSL",
    "SBER_QR_CSAM_LOGIN_URL",
    "SBER_QR_CSAM_LOGIN_BODY",
    "SBER_QR_CSAM_USER_AGENT",
    "SBER_QR_CSAM_REFERER",
    "SBER_QR_CSAM_VERIFY_SSL",
    "SBER_QR_APP_VERSION",
    "SBER_QR_SKIP_DOTENV_WRITE",
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


def _quote_dotenv_value(val: str) -> str:
    """Двойные кавычки и экранирование для безопасной записи в ``.env``."""
    escaped = val.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def patch_repo_dotenv(
    repo_root: Path,
    updates: Mapping[str, str],
    *,
    filename: str = ".env",
) -> bool:
    """
    Обновить или дописать строки ``KEY=value`` в ``repo_root/filename`` (UTF-8).
    Существующие непустые строки с тем же ключом заменяются; новые ключи дописываются в конец.
    Запись через временный файл + :meth:`Path.replace` в одном каталоге.
    """
    path = repo_root / filename
    if not path.is_file():
        return False
    pending: Dict[str, str] = {
        k: _quote_dotenv_value(v)
        for k, v in updates.items()
        if v is not None and str(v) != ""
    }
    if not pending:
        return True
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False
    lines = original.splitlines(keepends=True)
    matched: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append(line)
            continue
        s = stripped
        if s.startswith("export "):
            s = s[7:].lstrip()
        if "=" not in s:
            out.append(line)
            continue
        key, _, _ = s.partition("=")
        key = key.strip()
        if key in pending:
            nl = "\n" if line.endswith("\n") else ""
            out.append(f"{key}={pending[key]}{nl}")
            matched.add(key)
        else:
            out.append(line)
    for key, quoted in pending.items():
        if key not in matched:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(f"{key}={quoted}\n")
    new_text = "".join(out)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(new_text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True
