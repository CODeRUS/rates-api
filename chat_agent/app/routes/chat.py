# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Optional

import asyncio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

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
    audit = getattr(request.app.state, "audit_store", None)

    reply = ""
    err: Optional[str] = None
    reply_parse_mode: Optional[str] = None
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
        err = "Внутренняя ошибка сервиса."
        reply = ""
        reply_parse_mode = None

    if audit is not None:
        await audit.append_turn(
            user_id=body.user_id,
            user_message=body.message.strip(),
            assistant_message=reply or "",
            error=err,
            reply_parse_mode=reply_parse_mode if not err else None,
        )

    if err:
        return ChatResponse(reply=reply, error=err, reply_parse_mode=None)
    return ChatResponse(reply=reply, error=None, reply_parse_mode=reply_parse_mode)


@router.post("/chat/stream")
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    settings = request.app.state.settings
    secret_hdr = request.headers.get(HEADER_SECRET)
    if not _verify_secret(secret_hdr, settings.shared_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if len(body.message) > settings.max_message_chars:
        raise HTTPException(status_code=413, detail="Message too long")

    store = request.app.state.redis_store
    llm = request.app.state.llm_client
    audit = getattr(request.app.state, "audit_store", None)

    async def _events():
        q: asyncio.Queue[Optional[str]] = asyncio.Queue()
        end_marker = object()
        done: asyncio.Queue[object] = asyncio.Queue(maxsize=1)
        final_reply = ""
        final_err: Optional[str] = None
        final_mode: Optional[str] = None

        async def _on_chunk(ch: str) -> None:
            await q.put(ch)

        async def _produce() -> None:
            nonlocal final_reply, final_err, final_mode
            try:
                final_reply, final_err, final_mode = await run_chat_turn(
                    settings=settings,
                    store=store,
                    llm=llm,
                    user_id=body.user_id,
                    message=body.message.strip(),
                    include_env_system=body.include_env_system,
                    on_responder_chunk=_on_chunk,
                )
            except Exception:
                logger.exception("run_chat_turn(stream) failed")
                final_reply = ""
                final_err = "Внутренняя ошибка сервиса."
                final_mode = None
            if audit is not None:
                await audit.append_turn(
                    user_id=body.user_id,
                    user_message=body.message.strip(),
                    assistant_message=final_reply or "",
                    error=final_err,
                    reply_parse_mode=final_mode if not final_err else None,
                )
            await done.put(end_marker)

        producer = asyncio.create_task(_produce())
        try:
            while True:
                if not q.empty():
                    ch = await q.get()
                    if ch:
                        yield f"event: delta\ndata: {json.dumps({'delta': ch}, ensure_ascii=False)}\n\n"
                    continue
                if not done.empty():
                    _ = await done.get()
                    payload = {
                        "reply": final_reply,
                        "error": final_err,
                        "reply_parse_mode": None if final_err else final_mode,
                    }
                    yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                try:
                    ch = await asyncio.wait_for(q.get(), timeout=0.2)
                    if ch:
                        yield f"event: delta\ndata: {json.dumps({'delta': ch}, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    pass
        finally:
            if not producer.done():
                producer.cancel()

    return StreamingResponse(_events(), media_type="text/event-stream")
