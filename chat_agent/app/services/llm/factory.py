# -*- coding: utf-8 -*-
from __future__ import annotations

from chat_agent.app.config import Settings
from chat_agent.app.services.llm.base import LLMBackend
from chat_agent.app.services.llm.google_backend import GoogleBackend
from chat_agent.app.services.llm.openai_backend import OpenAIBackend


def build_llm_backend(settings: Settings) -> LLMBackend:
    if settings.llm_provider == "openai":
        key = (settings.openai_api_key or "").strip()
        url = (settings.openai_api_url or "").strip()
        if not key or not url:
            raise RuntimeError("OpenAI: задайте OPENAI_API_KEY и OPENAI_API_URL")
        return OpenAIBackend(api_key=key, api_url=url)
    if settings.llm_provider == "google":
        key = settings.google_key()
        if not key:
            raise RuntimeError("Google: задайте GOOGLE_API_KEY или GEMINI_API_KEY")
        return GoogleBackend(api_key=key)
    raise RuntimeError(f"Неизвестный CHAT_AGENT_LLM_PROVIDER: {settings.llm_provider}")
