# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from chat_agent.app.config import Settings

router = APIRouter(tags=["admin"])
HEADER_SECRET = "x-chat-agent-secret"


def _verify_secret(provided: Optional[str], expected: str) -> bool:
    if not provided or not expected:
        return False
    pa = provided.strip().encode("utf-8")
    eb = expected.encode("utf-8")
    if len(pa) > 256:
        return False
    ha = hmac.new(b"chat-agent-secret", pa, hashlib.sha256).digest()
    hb = hmac.new(b"chat-agent-secret", eb, hashlib.sha256).digest()
    return hmac.compare_digest(ha, hb)


def _client_is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower().strip()
    return h in ("127.0.0.1", "::1", "localhost", "0:0:0:0:0:0:0:1") or h.endswith(
        "127.0.0.1"
    )


def _require_localhost_and_secret(request: Request) -> None:
    client = request.client.host if request.client else None
    if not _client_is_loopback(client):
        raise HTTPException(status_code=403, detail="Admin доступен только с localhost")
    settings: Settings = request.app.state.settings
    if not _verify_secret(request.headers.get(HEADER_SECRET), settings.shared_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")


class AdminUserOut(BaseModel):
    user_id: str
    last_at: str


class AdminUsersResponse(BaseModel):
    users: List[AdminUserOut]


class AdminTurnOut(BaseModel):
    id: int
    created_at: str
    user_message: str
    assistant_message: str
    error: Optional[str] = None
    reply_parse_mode: Optional[str] = None
    llm_prompt_tokens: Optional[int] = None
    llm_completion_tokens: Optional[int] = None
    llm_total_tokens: Optional[int] = None
    llm_calls: Optional[int] = None
    llm_cost_usd: Optional[float] = None


class AdminHistoryResponse(BaseModel):
    turns: List[AdminTurnOut]
    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0


@router.get("/admin/api/users", response_model=AdminUsersResponse)
async def admin_users(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AdminUsersResponse:
    _require_localhost_and_secret(request)
    audit = getattr(request.app.state, "audit_store", None)
    if audit is None:
        raise HTTPException(status_code=503, detail="Аудит отключён или БД недоступна")
    rows = await audit.list_users(limit=limit, offset=offset)
    return AdminUsersResponse(
        users=[
            AdminUserOut(user_id=r.user_id, last_at=r.last_at.isoformat())
            for r in rows
        ]
    )


@router.get("/admin/api/history", response_model=AdminHistoryResponse)
async def admin_history(
    request: Request,
    user_id: str,
    before_id: Optional[int] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> AdminHistoryResponse:
    _require_localhost_and_secret(request)
    audit = getattr(request.app.state, "audit_store", None)
    if audit is None:
        raise HTTPException(status_code=503, detail="Аудит отключён или БД недоступна")
    uid = user_id.strip()
    if not uid or len(uid) > 32:
        raise HTTPException(status_code=400, detail="Некорректный user_id")
    turns = await audit.list_turns(user_id=uid, before_id=before_id, limit=limit)
    totals = await audit.usage_totals(user_id=uid)
    return AdminHistoryResponse(
        turns=[
            AdminTurnOut(
                id=t.id,
                created_at=t.created_at.isoformat(),
                user_message=t.user_message,
                assistant_message=t.assistant_message,
                error=t.error,
                reply_parse_mode=t.reply_parse_mode,
                llm_prompt_tokens=t.llm_prompt_tokens,
                llm_completion_tokens=t.llm_completion_tokens,
                llm_total_tokens=t.llm_total_tokens,
                llm_calls=t.llm_calls,
                llm_cost_usd=t.llm_cost_usd,
            )
            for t in turns
        ],
        total_cost_usd=totals.cost_usd,
        total_prompt_tokens=totals.prompt_tokens,
        total_completion_tokens=totals.completion_tokens,
        total_tokens=totals.total_tokens,
    )


def admin_static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "static" / "admin"


@router.get("/admin")
async def admin_page(request: Request) -> FileResponse:
    client = request.client.host if request.client else None
    if not _client_is_loopback(client):
        raise HTTPException(status_code=403, detail="Admin доступен только с localhost")
    index = admin_static_dir() / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404, detail="admin UI не найден")
    return FileResponse(index, media_type="text/html; charset=utf-8")
