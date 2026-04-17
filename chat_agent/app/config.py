# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional
from urllib.parse import quote

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _build_postgres_dsn(user: str, password: str, host: str, port: int, db: str) -> str:
    u = quote(user.strip(), safe="")
    p = quote(password, safe="")
    h = host.strip()
    if ":" in h and not h.startswith("["):
        host_part = f"[{h}]"
    else:
        host_part = h
    return f"postgresql://{u}:{p}@{host_part}:{port}/{quote(db.strip(), safe='')}"


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
    #: Принудительно добавлять --readonly ко всем вызовам rates.py из tool executor.
    tools_readonly: bool = Field(
        default=False, validation_alias="CHAT_AGENT_TOOLS_READONLY"
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
    #: Сколько последних user-реплик из Redis учитывать в **суффиксе** истории для planner (0 = без истории, только текущее сообщение).
    #: В промпт идут пары user/assistant из этого суффикса (сжатые по длине), затем отдельно текущая user-реплика.
    planner_user_history_turns: int = Field(
        default=0,
        validation_alias="CHAT_AGENT_PLANNER_USER_HISTORY_TURNS",
        ge=0,
        le=50,
    )
    #: Макс. символов текста одного сообщения в истории planner (0 = не усечь). Длинные ответы assistant укорачиваются.
    planner_history_message_max_chars: int = Field(
        default=1600,
        validation_alias="CHAT_AGENT_PLANNER_HISTORY_MSG_MAX",
        ge=0,
        le=8000,
    )
    #: Сколько последних сообщений из Redis передавать responder (0 = без истории).
    responder_history_messages: int = Field(
        default=8,
        validation_alias="CHAT_AGENT_RESPONDER_HISTORY_MESSAGES",
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

    #: Включить запись аудита в PostgreSQL (0/false — не подключаться к БД).
    audit_enabled_flag: bool = Field(
        default=True, validation_alias="CHAT_AGENT_AUDIT_ENABLED"
    )
    #: Полный URL переопределяет сборку из CHAT_AGENT_PG_* (SSL, нестандартный DSN).
    audit_database_url: Optional[str] = Field(
        default=None, validation_alias="CHAT_AGENT_DATABASE_URL"
    )
    pg_user: str = Field(default="rates", validation_alias="CHAT_AGENT_PG_USER")
    pg_password: str = Field(default="rates", validation_alias="CHAT_AGENT_PG_PASSWORD")
    pg_host: str = Field(default="127.0.0.1", validation_alias="CHAT_AGENT_PG_HOST")
    pg_port: int = Field(default=5432, validation_alias="CHAT_AGENT_PG_PORT", ge=1, le=65535)
    pg_db: str = Field(default="rates_chat", validation_alias="CHAT_AGENT_PG_DB")
    audit_retention_days: int = Field(
        default=30, validation_alias="CHAT_AGENT_AUDIT_RETENTION_DAYS", ge=1, le=3650
    )
    audit_max_text_chars: int = Field(
        default=32000, validation_alias="CHAT_AGENT_AUDIT_MAX_TEXT_CHARS", ge=1000, le=500_000
    )

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

    @field_validator("tools_readonly", mode="before")
    @classmethod
    def coerce_tools_readonly(cls, v: Any) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        s = str(v).strip().lower()
        if s in ("0", "false", "no", "off", ""):
            return False
        return True

    @field_validator("audit_enabled_flag", mode="before")
    @classmethod
    def coerce_audit_enabled_flag(cls, v: Any) -> bool:
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

    @model_validator(mode="after")
    def _resolve_audit_database_url(self) -> Settings:
        if not self.audit_enabled_flag:
            object.__setattr__(self, "audit_database_url", None)
            return self
        explicit = (self.audit_database_url or "").strip()
        if explicit:
            object.__setattr__(self, "audit_database_url", explicit)
            return self
        built = _build_postgres_dsn(
            self.pg_user,
            self.pg_password,
            self.pg_host,
            self.pg_port,
            self.pg_db,
        )
        object.__setattr__(self, "audit_database_url", built)
        return self

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

    def audit_enabled(self) -> bool:
        return self.audit_enabled_flag and bool((self.audit_database_url or "").strip())

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
