# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class UserbotSettings:
    api_id: int
    api_hash: str
    phone: str
    session_dir: Path
    bootstrap_messages_limit: int
    cache_ttl_sec: int


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def load_settings() -> UserbotSettings:
    api_id_raw = _env("USERBOT_API_ID")
    api_hash = _env("USERBOT_API_HASH")
    phone = _env("USERBOT_PHONE")
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

    try:
        ttl = int(_env("USERBOT_CACHE_TTL_SEC", "900"))
    except ValueError:
        ttl = 900
    ttl = max(30, ttl)

    return UserbotSettings(
        api_id=api_id,
        api_hash=api_hash,
        phone=phone,
        session_dir=sess_dir,
        bootstrap_messages_limit=n,
        cache_ttl_sec=ttl,
    )

