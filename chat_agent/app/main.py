# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI

from chat_agent.app.config import get_settings
from chat_agent.app.routes.chat import router as chat_router
from chat_agent.app.routes.health import router as health_router
from chat_agent.app.services.llm.factory import build_llm_backend
from chat_agent.app.services.llm_client import LLMClient
from chat_agent.app.services.redis_store import RedisStore

logger = logging.getLogger(__name__)

_LOG_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def _configure_chat_agent_logging() -> None:
    """
    Uvicorn поднимает root-логгер часто до WARNING — тогда INFO из приложения не видны в Docker.
    Вешаем обработчик на дерево ``chat_agent.*`` и не пробрасываем в root (без дублей).
    """
    pkg = logging.getLogger("chat_agent")
    pkg.setLevel(logging.INFO)
    pkg.propagate = False
    if pkg.handlers:
        return
    h = logging.StreamHandler(sys.stderr)
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter(_LOG_FMT))
    pkg.addHandler(h)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_chat_agent_logging()
    settings = get_settings()
    redis_client = redis.from_url(settings.redis_url, decode_responses=False)
    store = RedisStore(redis_client)
    backend = build_llm_backend(settings)
    llm = LLMClient(backend, settings)
    app.state.settings = settings
    app.state.redis = redis_client
    app.state.redis_store = store
    app.state.llm_client = llm
    if not settings.provider_config_ok():
        logger.error(
            "LLM provider configuration incomplete (CHAT_AGENT_LLM_PROVIDER=%s)",
            settings.llm_provider,
        )
    yield
    await redis_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="rates-api chat agent", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(chat_router)
    return app


app = create_app()
