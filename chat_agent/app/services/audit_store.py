# -*- coding: utf-8 -*-
"""Аудит пар user/assistant в PostgreSQL (только append + чтение для админки)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import asyncpg

from chat_agent.app.services.llm.base import LLMRequestUsage

logger = logging.getLogger(__name__)


def _clip(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + "\n… [усечено]"


@dataclass
class AuditUserRow:
    user_id: str
    last_at: datetime


@dataclass
class AuditTurnRow:
    id: int
    created_at: datetime
    user_message: str
    assistant_message: str
    error: Optional[str]
    reply_parse_mode: Optional[str]
    llm_prompt_tokens: Optional[int]
    llm_completion_tokens: Optional[int]
    llm_total_tokens: Optional[int]
    llm_calls: Optional[int]
    llm_cost_usd: Optional[float]


@dataclass
class AuditUsageTotals:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float


class AuditStore:
    def __init__(self, pool: asyncpg.Pool, *, max_text_chars: int) -> None:
        self._pool = pool
        self._max = max_text_chars

    async def append_turn(
        self,
        *,
        user_id: str,
        user_message: str,
        assistant_message: str,
        error: Optional[str],
        reply_parse_mode: Optional[str],
        llm_usage: Optional[LLMRequestUsage] = None,
    ) -> None:
        um = _clip(user_message or "", self._max)
        am = _clip(assistant_message or "", self._max)
        err = _clip(error or "", min(self._max, 8000)) if error else None
        mode = (reply_parse_mode or "").strip()[:16] or None
        usage = llm_usage or LLMRequestUsage()
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO chat_audit_turn
                        (
                            user_id,
                            user_message,
                            assistant_message,
                            error,
                            reply_parse_mode,
                            llm_prompt_tokens,
                            llm_completion_tokens,
                            llm_total_tokens,
                            llm_calls,
                            llm_cost_usd
                        )
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    user_id,
                    um,
                    am,
                    err,
                    mode,
                    int(usage.prompt_tokens),
                    int(usage.completion_tokens),
                    int(usage.total_tokens),
                    int(usage.calls),
                    float(usage.cost_usd),
                )
        except Exception:
            logger.exception("audit: не удалось записать turn user_id=%s", user_id)

    async def list_users(self, *, limit: int, offset: int) -> List[AuditUserRow]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT user_id, MAX(created_at) AS last_at
                FROM chat_audit_turn
                GROUP BY user_id
                ORDER BY last_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        out: List[AuditUserRow] = []
        for r in rows:
            ts = r["last_at"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append(AuditUserRow(user_id=str(r["user_id"]), last_at=ts))
        return out

    async def list_turns(
        self,
        *,
        user_id: str,
        before_id: Optional[int],
        limit: int,
    ) -> List[AuditTurnRow]:
        async with self._pool.acquire() as conn:
            if before_id is None:
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        created_at,
                        user_message,
                        assistant_message,
                        error,
                        reply_parse_mode,
                        llm_prompt_tokens,
                        llm_completion_tokens,
                        llm_total_tokens,
                        llm_calls,
                        llm_cost_usd
                    FROM chat_audit_turn
                    WHERE user_id = $1
                    ORDER BY id DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT
                        id,
                        created_at,
                        user_message,
                        assistant_message,
                        error,
                        reply_parse_mode,
                        llm_prompt_tokens,
                        llm_completion_tokens,
                        llm_total_tokens,
                        llm_calls,
                        llm_cost_usd
                    FROM chat_audit_turn
                    WHERE user_id = $1 AND id < $2
                    ORDER BY id DESC
                    LIMIT $3
                    """,
                    user_id,
                    before_id,
                    limit,
                )
        out: List[AuditTurnRow] = []
        for r in rows:
            ts = r["created_at"]
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append(
                AuditTurnRow(
                    id=int(r["id"]),
                    created_at=ts,
                    user_message=str(r["user_message"]),
                    assistant_message=str(r["assistant_message"]),
                    error=r["error"],
                    reply_parse_mode=r["reply_parse_mode"],
                    llm_prompt_tokens=r["llm_prompt_tokens"],
                    llm_completion_tokens=r["llm_completion_tokens"],
                    llm_total_tokens=r["llm_total_tokens"],
                    llm_calls=r["llm_calls"],
                    llm_cost_usd=r["llm_cost_usd"],
                )
            )
        out.reverse()
        return out

    async def total_cost_usd(self, *, user_id: str) -> float:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                "SELECT COALESCE(SUM(llm_cost_usd), 0) FROM chat_audit_turn WHERE user_id = $1",
                user_id,
            )
        return float(val or 0.0)

    async def usage_totals(self, *, user_id: str) -> AuditUsageTotals:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(llm_prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(llm_completion_tokens), 0) AS completion_tokens,
                    COALESCE(SUM(llm_total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(llm_cost_usd), 0) AS cost_usd
                FROM chat_audit_turn
                WHERE user_id = $1
                """,
                user_id,
            )
        return AuditUsageTotals(
            prompt_tokens=int(row["prompt_tokens"] or 0),
            completion_tokens=int(row["completion_tokens"] or 0),
            total_tokens=int(row["total_tokens"] or 0),
            cost_usd=float(row["cost_usd"] or 0.0),
        )

    async def purge_older_than_days(self, days: int) -> int:
        d = int(days)
        async with self._pool.acquire() as conn:
            status: str = await conn.execute(
                "DELETE FROM chat_audit_turn WHERE created_at < NOW() - ($1 * INTERVAL '1 day')",
                d,
            )
        try:
            return int(status.split()[-1])
        except (ValueError, IndexError):
            return 0
