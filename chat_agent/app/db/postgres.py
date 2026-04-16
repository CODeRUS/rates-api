# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)

_INIT_SQL = """
CREATE TABLE IF NOT EXISTS chat_audit_turn (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id TEXT NOT NULL,
    user_message TEXT NOT NULL,
    assistant_message TEXT NOT NULL,
    error TEXT NULL,
    reply_parse_mode TEXT NULL,
    llm_prompt_tokens INTEGER NULL,
    llm_completion_tokens INTEGER NULL,
    llm_total_tokens INTEGER NULL,
    llm_calls INTEGER NULL
);
ALTER TABLE chat_audit_turn
    ADD COLUMN IF NOT EXISTS llm_prompt_tokens INTEGER NULL;
ALTER TABLE chat_audit_turn
    ADD COLUMN IF NOT EXISTS llm_completion_tokens INTEGER NULL;
ALTER TABLE chat_audit_turn
    ADD COLUMN IF NOT EXISTS llm_total_tokens INTEGER NULL;
ALTER TABLE chat_audit_turn
    ADD COLUMN IF NOT EXISTS llm_calls INTEGER NULL;
CREATE INDEX IF NOT EXISTS chat_audit_turn_user_created_idx
    ON chat_audit_turn (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS chat_audit_turn_created_idx
    ON chat_audit_turn (created_at);
"""


async def create_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    async with pool.acquire() as conn:
        await conn.execute(_INIT_SQL)
    logger.info("PostgreSQL audit pool ready, schema ensured")
    return pool


async def close_pool(pool: Optional[asyncpg.Pool]) -> None:
    if pool is not None:
        await pool.close()
