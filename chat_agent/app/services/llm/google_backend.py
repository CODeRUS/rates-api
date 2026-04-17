# -*- coding: utf-8 -*-
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Callable, Literal, Optional
import logging

from chat_agent.app.services.llm.base import LLMBackend, LLMCompletion, LLMUsage

logger = logging.getLogger(__name__)


def _is_retryable_503(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code == 503:
        return True
    s = str(exc).upper()
    return "503" in s and "UNAVAILABLE" in s


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
        from google.genai import errors as genai_errors

        # SDK синхронный — не блокируем event loop; общий таймаут запроса.
        # На 503 делаем короткие повторы с backoff.
        max_attempts = 3
        backoff_sec = (0.8, 1.6)
        for attempt in range(1, max_attempts + 1):
            try:
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
            except genai_errors.ServerError as e:
                if not _is_retryable_503(e) or attempt >= max_attempts:
                    raise
                wait_sec = backoff_sec[attempt - 1]
                logger.warning(
                    "Gemini 503 on complete (attempt %d/%d), retry in %.1fs",
                    attempt,
                    max_attempts,
                    wait_sec,
                )
                await asyncio.sleep(wait_sec)

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_sec: float,
        on_usage: Optional[Callable[[LLMUsage], None]] = None,
    ) -> AsyncIterator[str]:
        import asyncio
        from google import genai
        from google.genai import errors as genai_errors
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

        max_attempts = 3
        backoff_sec = (0.8, 1.6)
        stream = None
        for attempt in range(1, max_attempts + 1):
            try:
                stream = await asyncio.wait_for(
                    client.aio.models.generate_content_stream(
                        model=model,
                        contents=contents,
                        config=config,
                    ),
                    timeout=timeout_sec,
                )
                break
            except genai_errors.ServerError as e:
                if not _is_retryable_503(e) or attempt >= max_attempts:
                    raise
                wait_sec = backoff_sec[attempt - 1]
                logger.warning(
                    "Gemini 503 on stream start (attempt %d/%d), retry in %.1fs",
                    attempt,
                    max_attempts,
                    wait_sec,
                )
                await asyncio.sleep(wait_sec)
        if stream is None:
            raise RuntimeError("Gemini stream not initialized")
        last_usage: Optional[LLMUsage] = None
        async for chunk in stream:
            um = getattr(chunk, "usage_metadata", None)
            if um is not None:
                last_usage = LLMUsage(
                    prompt_tokens=getattr(um, "prompt_token_count", None),
                    completion_tokens=getattr(um, "candidates_token_count", None),
                    total_tokens=getattr(um, "total_token_count", None),
                )
            txt = getattr(chunk, "text", None) or ""
            if txt:
                yield txt
        if on_usage is not None and last_usage is not None:
            on_usage(last_usage)
