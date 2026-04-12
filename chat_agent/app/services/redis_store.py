# -*- coding: utf-8 -*-
"""Redis: история диалога и кеш инструментов. Все ключи с префиксом tg:{user_id}:."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, List, Optional, Sequence

import redis.asyncio as redis

KEY_PREFIX_TEMPLATE = "tg:{user_id}:"


def _prefix(user_id: str) -> str:
    return KEY_PREFIX_TEMPLATE.format(user_id=user_id)


def messages_key(user_id: str) -> str:
    return f"{_prefix(user_id)}messages"


def tool_cache_key_redis(user_id: str, tool: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps({"tool": tool, "args": arguments}, sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{_prefix(user_id)}toolcache:{h}"


def rate_limit_key(user_id: str, minute_bucket: str) -> str:
    return f"{_prefix(user_id)}rl:{minute_bucket}"


class RedisStore:
    def __init__(self, client: redis.Redis) -> None:
        self._r = client

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except Exception:
            return False

    async def append_exchange(
        self,
        user_id: str,
        user_text: str,
        assistant_text: str,
        *,
        session_ttl_sec: int,
        max_pairs: int,
    ) -> None:
        """
        Сохраняем пару сообщений. В списке порядок: от новых к старым (LPUSH).
        max_pairs — максимум пар (после добавления обрезаем).
        """
        key = messages_key(user_id)
        payload = json.dumps(
            {"role": "user", "content": user_text},
            ensure_ascii=False,
        )
        payload_a = json.dumps(
            {"role": "assistant", "content": assistant_text},
            ensure_ascii=False,
        )
        pipe = self._r.pipeline()
        pipe.lpush(key, payload_a)
        pipe.lpush(key, payload)
        # 2 записи на пару; храним не более max_pairs * 2 сообщений
        keep = max(2, max_pairs * 2)
        pipe.ltrim(key, 0, keep - 1)
        pipe.expire(key, session_ttl_sec)
        await pipe.execute()

    async def get_recent_messages(
        self, user_id: str, limit: int
    ) -> List[dict[str, str]]:
        """Возвращает до ``limit`` последних сообщений в хронологическом порядке (старые → новые)."""
        key = messages_key(user_id)
        raw_list: Sequence[Optional[bytes]] = await self._r.lrange(key, 0, max(limit, 1) - 1)
        out: List[dict[str, str]] = []
        for b in reversed(raw_list):
            if not b:
                continue
            try:
                obj = json.loads(b.decode("utf-8"))
                if isinstance(obj, dict) and "role" in obj and "content" in obj:
                    out.append(
                        {
                            "role": str(obj["role"]),
                            "content": str(obj["content"])[:8000],
                        }
                    )
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return out

    async def touch_session_ttl(self, user_id: str, session_ttl_sec: int) -> None:
        await self._r.expire(messages_key(user_id), session_ttl_sec)

    async def get_tool_cache(self, user_id: str, tool: str, arguments: dict[str, Any]) -> Optional[str]:
        key = tool_cache_key_redis(user_id, tool, arguments)
        b = await self._r.get(key)
        if b is None:
            return None
        try:
            return b.decode("utf-8")
        except UnicodeDecodeError:
            return None

    async def set_tool_cache(
        self,
        user_id: str,
        tool: str,
        arguments: dict[str, Any],
        value: str,
        ttl_sec: int,
    ) -> None:
        key = tool_cache_key_redis(user_id, tool, arguments)
        await self._r.setex(key, ttl_sec, value.encode("utf-8"))

    async def check_rate_limit(
        self,
        user_id: str,
        *,
        limit_per_minute: int,
        bucket_ttl_sec: int = 90,
    ) -> bool:
        if limit_per_minute <= 0:
            return True
        bucket = str(int(time.time()) // 60)
        key = rate_limit_key(user_id, bucket)
        n = await self._r.incr(key)
        if n == 1:
            await self._r.expire(key, bucket_ttl_sec)
        return n <= limit_per_minute
