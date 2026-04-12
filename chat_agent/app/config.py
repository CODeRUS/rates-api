# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_repo_root() -> Path:
    # chat_agent/app/config.py → repo root
    return Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = Field(validation_alias="REDIS_URL")
    shared_secret: str = Field(validation_alias="CHAT_AGENT_SHARED_SECRET")

    llm_provider: Literal["openai", "google"] = Field(
        default="openai", validation_alias="CHAT_AGENT_LLM_PROVIDER"
    )

    repo_root: Path = Field(default_factory=_default_repo_root)

    session_ttl_sec: int = Field(default=3600, validation_alias="CHAT_AGENT_SESSION_TTL")
    cache_ttl_sec: int = Field(default=90, validation_alias="CHAT_AGENT_CACHE_TTL")
    max_history_messages: int = Field(
        default=20, validation_alias="CHAT_AGENT_MAX_HISTORY"
    )
    max_message_chars: int = Field(
        default=8000, validation_alias="CHAT_AGENT_MAX_MESSAGE_CHARS"
    )
    llm_timeout_sec: float = Field(
        default=120.0, validation_alias="CHAT_AGENT_LLM_TIMEOUT_SEC"
    )
    tool_timeout_sec: float = Field(
        default=60.0, validation_alias="CHAT_AGENT_TOOL_TIMEOUT_SEC"
    )
    rate_limit_per_minute: int = Field(
        default=45, validation_alias="CHAT_AGENT_RATE_LIMIT_PER_MINUTE"
    )

    #: Пошаговые логи: сообщение пользователя, planner, инструмент, контекст LLM.
    pipeline_log: bool = Field(default=True, validation_alias="CHAT_AGENT_PIPELINE_LOG")
    #: Макс. символов при логировании вывода rates.py (0 = без лимита).
    log_tool_output_max: int = Field(
        default=50_000, validation_alias="CHAT_AGENT_LOG_TOOL_OUTPUT_MAX"
    )
    #: Макс. символов JSON-снимка messages для planner/responder (0 = без лимита).
    log_llm_messages_max: int = Field(
        default=120_000, validation_alias="CHAT_AGENT_LOG_LLM_MESSAGES_MAX"
    )
    #: Сколько прошлых реплик пользователя передать в planner (0 = только текущее сообщение).
    #: Старые user-сообщения с суммами/параметрами заставляли модель повторять arguments и кеш инструмента.
    planner_user_history_turns: int = Field(
        default=0,
        validation_alias="CHAT_AGENT_PLANNER_USER_HISTORY_TURNS",
        ge=0,
        le=50,
    )

    openai_api_key: Optional[str] = Field(default=None, validation_alias="OPENAI_API_KEY")
    openai_api_url: Optional[str] = Field(default=None, validation_alias="OPENAI_API_URL")
    openai_model: str = Field(default="gpt-4o-mini", validation_alias="OPENAI_MODEL")
    openai_planner_model: Optional[str] = Field(
        default=None, validation_alias="OPENAI_PLANNER_MODEL"
    )
    openai_responder_model: Optional[str] = Field(
        default=None, validation_alias="OPENAI_RESPONDER_MODEL"
    )

    google_api_key: Optional[str] = Field(
        default=None, validation_alias="GOOGLE_API_KEY"
    )
    gemini_api_key: Optional[str] = Field(
        default=None, validation_alias="GEMINI_API_KEY"
    )
    gemini_model: str = Field(default="gemini-2.0-flash", validation_alias="GEMINI_MODEL")
    gemini_planner_model: Optional[str] = Field(
        default=None, validation_alias="GEMINI_PLANNER_MODEL"
    )
    gemini_responder_model: Optional[str] = Field(
        default=None, validation_alias="GEMINI_RESPONDER_MODEL"
    )

    host: str = Field(default="0.0.0.0", validation_alias="CHAT_AGENT_HOST")
    port: int = Field(default=18880, validation_alias="CHAT_AGENT_PORT")

    @field_validator("pipeline_log", mode="before")
    @classmethod
    def coerce_pipeline_log(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return True
        s = str(v).strip().lower()
        if s in ("0", "false", "no", "off", ""):
            return False
        return True

    @field_validator("shared_secret")
    @classmethod
    def secret_non_empty(cls, v: str) -> str:
        if not (v or "").strip():
            raise ValueError("CHAT_AGENT_SHARED_SECRET must be non-empty")
        return v.strip()

    def effective_openai_planner_model(self) -> str:
        return (self.openai_planner_model or self.openai_model).strip()

    def effective_openai_responder_model(self) -> str:
        return (self.openai_responder_model or self.openai_model).strip()

    def effective_gemini_planner_model(self) -> str:
        return (self.gemini_planner_model or self.gemini_model).strip()

    def effective_gemini_responder_model(self) -> str:
        return (self.gemini_responder_model or self.gemini_model).strip()

    def google_key(self) -> Optional[str]:
        return (self.google_api_key or self.gemini_api_key or "").strip() or None

    def provider_config_ok(self) -> bool:
        if self.llm_provider == "openai":
            return bool(
                (self.openai_api_key or "").strip() and (self.openai_api_url or "").strip()
            )
        if self.llm_provider == "google":
            return bool(self.google_key())
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
