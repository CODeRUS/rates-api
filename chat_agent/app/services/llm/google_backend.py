# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from chat_agent.app.services.llm.base import LLMBackend, LLMCompletion, LLMUsage


def _sync_gemini_complete(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    mode: Literal["text", "json"],
) -> LLMCompletion:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    system_chunks = [m["content"] for m in messages if m.get("role") == "system"]
    system_instruction = "\n\n".join(system_chunks) if system_chunks else None
    contents: list[types.Content] = []
    for m in messages:
        if m.get("role") == "system":
            continue
        role = "user" if m.get("role") == "user" else "model"
        text = (m.get("content") or "").strip()
        if not text:
            continue
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=text)])
        )
    if not contents:
        contents.append(
            types.Content(role="user", parts=[types.Part.from_text(text=".")])
        )
    cfg_kw: dict = {}
    if system_instruction:
        cfg_kw["system_instruction"] = system_instruction
    if mode == "json":
        cfg_kw["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**cfg_kw) if cfg_kw else None
    resp = client.models.generate_content(
        model=model,
        contents=contents,
        config=config,
    )
    text = (resp.text or "").strip()
    usage = LLMUsage()
    um = getattr(resp, "usage_metadata", None)
    if um is not None:
        usage = LLMUsage(
            prompt_tokens=getattr(um, "prompt_token_count", None),
            completion_tokens=getattr(um, "candidates_token_count", None),
            total_tokens=getattr(um, "total_token_count", None),
        )
    return LLMCompletion(text=text, usage=usage)


class GoogleBackend:
    def __init__(self, *, api_key: str) -> None:
        self._api_key = api_key

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        mode: Literal["text", "json"],
        model: str,
        timeout_sec: float,
    ) -> LLMCompletion:
        import asyncio

        # SDK синхронный — не блокируем event loop; общий таймаут запроса.
        return await asyncio.wait_for(
            asyncio.to_thread(
                _sync_gemini_complete,
                api_key=self._api_key,
                model=model,
                messages=messages,
                mode=mode,
            ),
            timeout=timeout_sec,
        )

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_sec: float,
    ) -> AsyncIterator[str]:
        import asyncio
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self._api_key)
        system_chunks = [m["content"] for m in messages if m.get("role") == "system"]
        system_instruction = "\n\n".join(system_chunks) if system_chunks else None
        contents: list[types.Content] = []
        for m in messages:
            if m.get("role") == "system":
                continue
            role = "user" if m.get("role") == "user" else "model"
            text = (m.get("content") or "").strip()
            if not text:
                continue
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=text)])
            )
        if not contents:
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=".")])
            )
        cfg_kw: dict = {}
        if system_instruction:
            cfg_kw["system_instruction"] = system_instruction
        config = types.GenerateContentConfig(**cfg_kw) if cfg_kw else None

        stream = await asyncio.wait_for(
            client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config,
            ),
            timeout=timeout_sec,
        )
        async for chunk in stream:
            txt = getattr(chunk, "text", None) or ""
            if txt:
                yield txt
