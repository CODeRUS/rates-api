# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

_MAX_PLANNER_TOOL_STEPS = 5

_USER_ID_RE = re.compile(r"^[0-9]{1,20}$")


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=20)
    message: str = Field(..., min_length=1)
    include_env_system: bool = True

    @field_validator("user_id")
    @classmethod
    def digits_only(cls, v: str) -> str:
        s = v.strip()
        if not _USER_ID_RE.match(s):
            raise ValueError("user_id must match ^[0-9]{1,20}$")
        return s


class ChatResponse(BaseModel):
    reply: str = ""
    error: Optional[str] = None
    #: Когда ответ сформирован моделью-ответчиком — «html» (Telegram HTML); иначе None (plain).
    reply_parse_mode: Optional[Literal["html"]] = None


class PlannerToolStep(BaseModel):
    """Один вызов инструмента в цепочке `tool_steps`."""

    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class PlannerOutput(BaseModel):
    """Строгий JSON от planner LLM."""

    tool: str = Field(default="none", description="Имя инструмента из whitelist или none")
    arguments: dict[str, Any] = Field(default_factory=dict)
    needs_tool: bool = False
    #: true — нужны рассуждение/расчёт на основе вывода инструмента; false — однозначный запрос, в основном отдать результат команды.
    think: bool = False
    #: Несколько вызовов подряд (до 5), если одной команды мало; тогда исполняются все шаги до responder.
    tool_steps: Optional[list[PlannerToolStep]] = None
    #: Обязателен в JSON планировщика: true — вне темы бота; false — в теме (даже если tool=none).
    out_of_scope: bool = Field(
        ...,
        description="true если вопрос не про курсы/обмен из каталога; false если в теме",
    )

    @field_validator("tool_steps", mode="before")
    @classmethod
    def _tool_steps_empty_to_none(cls, v: Any) -> Any:
        if v is None or v == []:
            return None
        return v

    @field_validator("tool_steps")
    @classmethod
    def _tool_steps_max_len(cls, v: Optional[list[PlannerToolStep]]) -> Optional[list[PlannerToolStep]]:
        if v is not None and len(v) > _MAX_PLANNER_TOOL_STEPS:
            raise ValueError(f"tool_steps: не более {_MAX_PLANNER_TOOL_STEPS} шагов")
        return v
