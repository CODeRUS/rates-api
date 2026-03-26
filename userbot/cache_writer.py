# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable

import rates_unified_cache as ucc
from userbot.models import ParsedRate

DEFAULT_USERBOT_TTL_SEC = 365 * 24 * 60 * 60


def key_for_source(source_id: str) -> str:
    return "chatcash:" + source_id


def write_source_snapshot(
    *,
    source_id: str,
    rows: Iterable[ParsedRate],
    ttl_sec: int = DEFAULT_USERBOT_TTL_SEC,
) -> None:
    doc = ucc.load_unified()
    payload_rows = []
    for r in rows:
        payload_rows.append(
            {
                "source_id": r.source_id,
                "source_name": r.source_name,
                "currency": r.currency,
                "category": r.category,
                "rate": r.rate,
                "message_id": r.message_id,
                "message_unix": r.message_unix,
                "chat": r.chat,
            }
        )
    if not payload_rows:
        return
    ucc.l1_set(doc, key_for_source(source_id), payload_rows, ttl_sec=ttl_sec)
    ucc.save_unified(doc)

