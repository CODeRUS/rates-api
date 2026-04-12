# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from chat_agent.app.config import get_settings
from chat_agent.app.db.postgres import close_pool, create_pool
from chat_agent.app.routes.admin import admin_static_dir, router as admin_router
from chat_agent.app.routes.chat import router as chat_router
from chat_agent.app.routes.health import router as health_router
from chat_agent.app.services.audit_store import AuditStore
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


async def _audit_retention_loop(settings, audit: AuditStore) -> None:
    while True:
        try:
            await asyncio.sleep(3600)
            n = await audit.purge_older_than_days(settings.audit_retention_days)
            if n:
                logger.info(
                    "audit retention: удалено записей: %d (старше %d дн.)",
                    n,
                    settings.audit_retention_days,
                )
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("audit retention failed")


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
    app.state.pg_pool = None
    app.state.audit_store = None
    app.state._audit_retention_task = None

    if settings.audit_enabled():
        try:
            pool = await create_pool(settings.audit_database_url.strip())
            app.state.pg_pool = pool
            audit = AuditStore(pool, max_text_chars=settings.audit_max_text_chars)
            app.state.audit_store = audit
            n0 = await audit.purge_older_than_days(settings.audit_retention_days)
            if n0:
                logger.info("audit: при старте удалено устаревших записей: %d", n0)
            app.state._audit_retention_task = asyncio.create_task(
                _audit_retention_loop(settings, audit)
            )
        except Exception:
            logger.exception(
                "PostgreSQL аудит недоступен (CHAT_AGENT_PG_* / CHAT_AGENT_DATABASE_URL); "
                "чат работает без записи в БД"
            )

    if not settings.provider_config_ok():
        logger.error(
            "LLM provider configuration incomplete (CHAT_AGENT_LLM_PROVIDER=%s)",
            settings.llm_provider,
        )
    yield
    t = getattr(app.state, "_audit_retention_task", None)
    if t is not None:
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    await close_pool(app.state.pg_pool)
    await redis_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="rates-api chat agent", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(admin_router)
    app.mount(
        "/admin/static",
        StaticFiles(directory=str(admin_static_dir())),
        name="admin_static",
    )
    return app


app = create_app()
