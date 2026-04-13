# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator

from chat_agent.app.config import Settings
from chat_agent.app.services.llm.base import LLMBackend, LLMCompletion


class LLMClient:
    def __init__(self, backend: LLMBackend, settings: Settings) -> None:
        self._b = backend
        self._s = settings

    def _planner_model(self) -> str:
        if self._s.llm_provider == "openai":
            return self._s.effective_openai_planner_model()
        return self._s.effective_gemini_planner_model()

    def _responder_model(self) -> str:
        if self._s.llm_provider == "openai":
            return self._s.effective_openai_responder_model()
        return self._s.effective_gemini_responder_model()

    def planner_model_name(self) -> str:
        """Имя модели для логов / отладки."""
        return self._planner_model()

    def responder_model_name(self) -> str:
        return self._responder_model()

    async def plan(self, messages: list[dict[str, str]]) -> LLMCompletion:
        return await self._b.complete(
            messages,
            mode="json",
            model=self._planner_model(),
            timeout_sec=self._s.llm_timeout_sec,
        )

    async def respond(self, messages: list[dict[str, str]]) -> LLMCompletion:
        return await self._b.complete(
            messages,
            mode="text",
            model=self._responder_model(),
            timeout_sec=self._s.llm_timeout_sec,
        )

    async def respond_stream(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[str]:
        if hasattr(self._b, "stream_complete"):
            async for chunk in self._b.stream_complete(
                messages,
                model=self._responder_model(),
                timeout_sec=self._s.llm_timeout_sec,
            ):
                if chunk:
                    yield chunk
            return
        comp = await self.respond(messages)
        if comp.text:
            yield comp.text
