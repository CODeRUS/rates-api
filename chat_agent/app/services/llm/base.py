# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class LLMUsage:
    """Статистика токенов одного вызова completion (если провайдер отдал)."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass(frozen=True)
class LLMCompletion:
    """Текст ответа модели и usage для логов."""

    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


@runtime_checkable
class LLMBackend(Protocol):
    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        mode: Literal["text", "json"],
        model: str,
        timeout_sec: float,
    ) -> LLMCompletion: ...

    async def stream_complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        timeout_sec: float,
    ) -> AsyncIterator[str]: ...
