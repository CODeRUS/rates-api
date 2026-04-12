# -*- coding: utf-8 -*-
from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    store = request.app.state.redis_store
    redis_ok = await store.ping()
    prov_ok = settings.provider_config_ok()
    return {
        "ok": redis_ok and prov_ok,
        "redis": redis_ok,
        "provider": settings.llm_provider,
        "provider_config_ok": prov_ok,
    }
