from __future__ import annotations

import asyncio
from types import SimpleNamespace

from chat_agent.app.db.postgres import _INIT_SQL
from chat_agent.app.routes import chat as chat_route
from chat_agent.app.schemas.chat import ChatRequest
from chat_agent.app.services.audit_store import AuditStore
from chat_agent.app.services.llm.base import LLMRequestUsage


class _FakeConn:
    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.rows = []

    async def execute(self, sql: str, *args: object) -> str:
        self.execute_calls.append((sql, args))
        return "INSERT 0 1"

    async def fetch(self, sql: str, *args: object):
        _ = (sql, args)
        return self.rows


class _AcquireCtx:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        _ = (exc_type, exc, tb)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx(self._conn)


def test_init_sql_adds_token_columns_for_existing_tables() -> None:
    assert "ADD COLUMN IF NOT EXISTS llm_prompt_tokens" in _INIT_SQL
    assert "ADD COLUMN IF NOT EXISTS llm_completion_tokens" in _INIT_SQL
    assert "ADD COLUMN IF NOT EXISTS llm_total_tokens" in _INIT_SQL
    assert "ADD COLUMN IF NOT EXISTS llm_calls" in _INIT_SQL


def test_audit_store_append_turn_persists_request_token_totals() -> None:
    conn = _FakeConn()
    store = AuditStore(_FakePool(conn), max_text_chars=500)

    asyncio.run(
        store.append_turn(
            user_id="123",
            user_message="курс",
            assistant_message="ответ",
            error=None,
            reply_parse_mode="html",
            llm_usage=LLMRequestUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                calls=2,
            ),
        )
    )

    assert len(conn.execute_calls) == 1
    sql, args = conn.execute_calls[0]
    assert "llm_prompt_tokens" in sql
    assert args[-4:] == (10, 5, 15, 2)


def test_audit_store_list_turns_reads_token_totals() -> None:
    conn = _FakeConn()
    conn.rows = [
        {
            "id": 7,
            "created_at": __import__("datetime").datetime(2026, 4, 16),
            "user_message": "курс",
            "assistant_message": "ответ",
            "error": None,
            "reply_parse_mode": "html",
            "llm_prompt_tokens": 10,
            "llm_completion_tokens": 5,
            "llm_total_tokens": 15,
            "llm_calls": 2,
        }
    ]
    store = AuditStore(_FakePool(conn), max_text_chars=500)

    rows = asyncio.run(store.list_turns(user_id="123", before_id=None, limit=10))

    assert len(rows) == 1
    assert rows[0].llm_prompt_tokens == 10
    assert rows[0].llm_completion_tokens == 5
    assert rows[0].llm_total_tokens == 15
    assert rows[0].llm_calls == 2


def test_chat_route_passes_llm_usage_to_audit(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_chat_turn(**kwargs):
        _ = kwargs
        return (
            "готово",
            None,
            "html",
            LLMRequestUsage(
                prompt_tokens=100,
                completion_tokens=25,
                total_tokens=125,
                calls=2,
            ),
        )

    class FakeAudit:
        async def append_turn(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(chat_route, "run_chat_turn", fake_run_chat_turn)
    request = SimpleNamespace(
        headers={"x-chat-agent-secret": "secret"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                settings=SimpleNamespace(shared_secret="secret", max_message_chars=1000),
                redis_store=object(),
                llm_client=object(),
                audit_store=FakeAudit(),
            )
        ),
    )
    body = ChatRequest(user_id="123", message="курс")

    resp = asyncio.run(chat_route.chat(request, body))

    assert resp.reply == "готово"
    assert captured["llm_usage"] == LLMRequestUsage(
        prompt_tokens=100,
        completion_tokens=25,
        total_tokens=125,
        calls=2,
    )
