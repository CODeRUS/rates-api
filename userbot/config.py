# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserbotSettings:
    api_id: int
    api_hash: str
    session_dir: Path
    bootstrap_messages_limit: int
    device_model: str
    system_version: str
    app_version: str
    lang_code: str


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def load_settings() -> UserbotSettings:
    api_id_raw = _env("USERBOT_API_ID")
    api_hash = _env("USERBOT_API_HASH")
    if not api_id_raw or not api_hash:
        raise RuntimeError("USERBOT_API_ID/USERBOT_API_HASH не заданы")
    try:
        api_id = int(api_id_raw)
    except ValueError as e:
        raise RuntimeError("USERBOT_API_ID должен быть числом") from e

    sess_dir = Path(_env("USERBOT_SESSION_DIR", "/data/userbot"))
    sess_dir.mkdir(parents=True, exist_ok=True)

    try:
        n = int(_env("USERBOT_BOOTSTRAP_MESSAGES_LIMIT", "5"))
    except ValueError:
        n = 5
    n = max(1, n)
    device_model = _env("USERBOT_DEVICE_MODEL", "Samsung SM-S918B")
    system_version = _env("USERBOT_SYSTEM_VERSION", "Android 14")
    app_version = _env("USERBOT_APP_VERSION", "10.13.4")
    lang_code = _env("USERBOT_LANG_CODE", "ru")

    return UserbotSettings(
        api_id=api_id,
        api_hash=api_hash,
        session_dir=sess_dir,
        bootstrap_messages_limit=n,
        device_model=device_model,
        system_version=system_version,
        app_version=app_version,
        lang_code=lang_code,
    )

