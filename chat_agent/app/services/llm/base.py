# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable


@runtime_checkable
class LLMBackend(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        mode: Literal["text", "json"],
        model: str,
        timeout_sec: float,
    ) -> str: ...
