# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal

import httpx

from chat_agent.app.services.llm.base import LLMBackend


class OpenAIBackend:
    def __init__(self, *, api_key: str, api_url: str) -> None:
        self._api_key = api_key
        self._api_url = api_url.strip()

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        mode: Literal["text", "json"],
        model: str,
        timeout_sec: float,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict = {"model": model, "messages": messages}
        if mode == "json":
            payload["response_format"] = {"type": "json_object"}
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            r = await client.post(self._api_url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(f"OpenAI response shape unexpected: {data!r}") from e
