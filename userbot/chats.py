# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, Optional, Sequence

# Telethon marked channel id: -(channel_id + 10**12) → -100XXXXXXXXXX
_CHANNEL_MARK = 10**12


def normalize_chat_ref(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("@"):
        return "@" + s[1:].lower()
    return s


def _unique(keys: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for k in keys:
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return tuple(out)


def peer_id_keys(chat_id: int) -> tuple[str, ...]:
    keys = [str(chat_id)]
    if chat_id < 0:
        n = -chat_id
        if n >= _CHANNEL_MARK:
            keys.append(str(n - _CHANNEL_MARK))
        else:
            keys.append(str(n))
    else:
        keys.append(str(-(chat_id + _CHANNEL_MARK)))
        keys.append(str(-chat_id))
    return _unique(keys)


def event_chat_keys(
    *,
    username: Optional[str] = None,
    chat_id: Optional[int] = None,
) -> tuple[str, ...]:
    keys: list[str] = []
    if username:
        keys.append(normalize_chat_ref("@" + str(username).lstrip("@")))
    if chat_id is not None:
        keys.extend(peer_id_keys(int(chat_id)))
    return _unique(keys)


def keys_from_event(event: object) -> tuple[str, ...]:
    chat = getattr(event, "chat", None)
    username = getattr(chat, "username", None) if chat is not None else None
    chat_id = getattr(event, "chat_id", None)
    if chat_id is None and chat is not None:
        chat_id = getattr(chat, "id", None)
    try:
        chat_id_int: Optional[int] = int(chat_id) if chat_id is not None else None
    except (TypeError, ValueError):
        chat_id_int = None
    return event_chat_keys(username=username, chat_id=chat_id_int)


def lookup_source(cfg_by_chat: dict, keys: Sequence[str]):
    for key in keys:
        hit = cfg_by_chat.get(key)
        if hit is not None:
            return hit
    return None
