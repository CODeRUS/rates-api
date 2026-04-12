# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from chat_agent.app.schemas.chat import ChatRequest, ChatResponse
from chat_agent.app.services.orchestrator import run_chat_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

HEADER_SECRET = "x-chat-agent-secret"


def _verify_secret(provided: Optional[str], expected: str) -> bool:
    if not provided or not expected:
        return False
    pa = provided.strip().encode("utf-8")
    eb = expected.encode("utf-8")
    if len(pa) > 256:
        return False
    # Фиксированная длина для compare_digest: HMAC-SHA256
    ha = hmac.new(b"chat-agent-secret", pa, hashlib.sha256).digest()
    hb = hmac.new(b"chat-agent-secret", eb, hashlib.sha256).digest()
    return hmac.compare_digest(ha, hb)


@router.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    settings = request.app.state.settings
    secret_hdr = request.headers.get(HEADER_SECRET)
    if not _verify_secret(secret_hdr, settings.shared_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if len(body.message) > settings.max_message_chars:
        raise HTTPException(status_code=413, detail="Message too long")

    store = request.app.state.redis_store
    llm = request.app.state.llm_client

    try:
        reply, err, reply_parse_mode = await run_chat_turn(
            settings=settings,
            store=store,
            llm=llm,
            user_id=body.user_id,
            message=body.message.strip(),
            include_env_system=body.include_env_system,
        )
    except Exception:
        logger.exception("run_chat_turn failed")
        return ChatResponse(
            reply="", error="Внутренняя ошибка сервиса.", reply_parse_mode=None
        )

    if err:
        return ChatResponse(reply=reply, error=err, reply_parse_mode=None)
    return ChatResponse(reply=reply, error=None, reply_parse_mode=reply_parse_mode)
