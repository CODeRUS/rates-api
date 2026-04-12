# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from chat_agent.app.config import Settings
from chat_agent.app.prompts.planner_system import build_planner_system, build_responder_system
from chat_agent.app.schemas.chat import PlannerOutput
from chat_agent.app.services.llm_client import LLMClient
from chat_agent.app.services.redis_store import RedisStore
from chat_agent.app.pipeline_log import clip_text, messages_for_log
from chat_agent.app.prompts.commands_catalog import REGISTERED_TOOL_NAMES
from chat_agent.app.services.tools import execute_tool

logger = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_JSON_FENCE_END = re.compile(r"\s*```\s*$", re.DOTALL)


def _strip_json_fence(raw: str) -> str:
    s = raw.strip()
    s = _JSON_FENCE.sub("", s)
    s = _JSON_FENCE_END.sub("", s)
    return s.strip()


def _parse_planner_output(raw: str) -> PlannerOutput:
    cleaned = _strip_json_fence(raw)
    data = json.loads(cleaned)
    if not isinstance(data, dict):
        raise ValueError("planner JSON must be an object")
    return PlannerOutput.model_validate(data)


def _truncate(s: str, max_len: int) -> str:
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 20] + "\n… (усечено)"


def _tool_output_is_rates_error(text: str) -> bool:
    return text.lstrip().startswith("[rates.py код")


def _format_tool_results_for_context(
    exec_steps: list[tuple[str, dict[str, Any]]],
    raw_chunks: list[str],
) -> str:
    """Текст для responder / логов; без служебных заголовков вида === … ===."""
    if not raw_chunks:
        return ""
    if len(exec_steps) == 1:
        return raw_chunks[0]
    parts: list[str] = []
    for (tname, _), out in zip(exec_steps, raw_chunks):
        parts.append(f"Инструмент «{tname}»:\n{out}")
    return "\n\n".join(parts)


def _execution_steps(plan: PlannerOutput) -> Optional[list[tuple[str, dict[str, Any]]]]:
    """
    None — в плане недопустимое имя инструмента.
    [] — не вызывать rates.py.
    Иначе — список (tool, arguments) по порядку.
    """
    if plan.tool_steps:
        out: list[tuple[str, dict[str, Any]]] = []
        for s in plan.tool_steps:
            t = s.tool
            if t not in REGISTERED_TOOL_NAMES or t == "none":
                return None
            out.append((t, dict(s.arguments or {})))
        return out
    if plan.needs_tool and plan.tool != "none":
        if plan.tool not in REGISTERED_TOOL_NAMES:
            return None
        return [(plan.tool, dict(plan.arguments or {}))]
    return []


def _history_for_planner(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Только прошлые реплики пользователя (без assistant)."""
    return [dict(m) for m in history if m.get("role") == "user"]


def _planner_user_context(
    history: list[dict[str, str]], *, max_turns: int
) -> list[dict[str, str]]:
    """Последние N user-сообщений из истории для planner; max_turns=0 → пусто."""
    users = _history_for_planner(history)
    if max_turns <= 0:
        return []
    return users[-max_turns:]


async def run_chat_turn(
    *,
    settings: Settings,
    store: RedisStore,
    llm: LLMClient,
    user_id: str,
    message: str,
    include_env_system: bool,
) -> tuple[str, Optional[str], Optional[str]]:
    """
    Возвращает (reply, error, reply_parse_mode).
    ``reply_parse_mode`` — ``\"html\"``, если ответ от модели-ответчика оформлен под Telegram HTML; иначе ``None`` (plain).
    error установлен при сбое, reply может быть пустым.
    """
    import os

    extra = (os.environ.get("OPENAI_PROMPT") or "").strip()
    if not include_env_system:
        extra = ""

    if not await store.check_rate_limit(
        user_id,
        limit_per_minute=settings.rate_limit_per_minute,
    ):
        return "", "Слишком много запросов. Подождите минуту.", None

    history = await store.get_recent_messages(
        user_id, limit=settings.max_history_messages
    )

    def _pl(msg: str, *args: Any) -> None:
        if settings.pipeline_log:
            logger.info(msg, *args)

    _pl(
        "[pipeline] 1. сообщение пользователя user_id=%s include_env_system=%s message=%r",
        user_id,
        include_env_system,
        message,
    )
    _pl(
        "[pipeline] 1b. история из Redis: %d сообщений",
        len(history),
    )
    if history and settings.pipeline_log:
        logger.info(
            "[pipeline] 1c. история (сжато): %s",
            clip_text(
                json.dumps(history, ensure_ascii=False),
                settings.log_llm_messages_max,
            ),
        )

    planner_system = build_planner_system(extra_env_system=extra)
    planner_messages: list[dict[str, str]] = [{"role": "system", "content": planner_system}]
    planner_ctx = _planner_user_context(
        history, max_turns=settings.planner_user_history_turns
    )
    _pl(
        "[pipeline] 1d. в planner передано прошлых user-сообщений: %d (лимит CHAT_AGENT_PLANNER_USER_HISTORY_TURNS=%d)",
        len(planner_ctx),
        settings.planner_user_history_turns,
    )
    for m in planner_ctx:
        planner_messages.append(dict(m))
    planner_messages.append({"role": "user", "content": message})

    _pl(
        "[pipeline] 2. контекст для LLM (planner), модель=%s:\n%s",
        llm.planner_model_name(),
        messages_for_log(planner_messages, max_total=settings.log_llm_messages_max),
    )

    raw_plan = await llm.plan(planner_messages)
    plan: PlannerOutput
    try:
        plan = _parse_planner_output(raw_plan)
    except Exception as e1:
        logger.warning("planner JSON parse failed: %s; retry once", e1)
        fix_messages = list(planner_messages)
        fix_messages.append(
            {
                "role": "user",
                "content": (
                    "Исправь ответ: верни только один JSON-объект с ключами tool, arguments, needs_tool, think "
                    "и опционально tool_steps (массив объектов {tool, arguments} для 2–5 вызовов подряд). "
                    "Без markdown."
                ),
            }
        )
        _pl(
            "[pipeline] 2b. повтор planner, контекст:\n%s",
            messages_for_log(fix_messages, max_total=settings.log_llm_messages_max),
        )
        try:
            raw2 = await llm.plan(fix_messages)
            plan = _parse_planner_output(raw2)
            raw_plan = raw2
        except Exception as e2:
            logger.exception("planner retry failed: %s", e2)
            plan = PlannerOutput(tool="none", arguments={}, needs_tool=False, think=False)
            raw_plan = ""

    _pl(
        "[pipeline] 3. ответ planner (сырой JSON от модели): %s",
        clip_text(raw_plan or "(пусто)", 20_000),
    )

    exec_steps = _execution_steps(plan)
    if exec_steps is None:
        _pl(
            "[pipeline] 3b. tool/tool_steps вне whitelist — сброс на none",
            plan.tool,
        )
        plan = PlannerOutput(tool="none", arguments={}, needs_tool=False, think=False)
        exec_steps = []

    _pl(
        "[pipeline] 4. выбранное действие (после валидации) tool=%s needs_tool=%s think=%s arguments=%s tool_steps=%s",
        plan.tool,
        plan.needs_tool,
        plan.think,
        json.dumps(plan.arguments, sort_keys=True, ensure_ascii=False),
        [s.model_dump() for s in plan.tool_steps] if plan.tool_steps else None,
    )

    tool_result_full = ""
    raw_chunks: list[str] = []
    if exec_steps:

        async def _get(uid: str, t: str, a: dict[str, Any]) -> Optional[str]:
            return await store.get_tool_cache(uid, t, a)

        async def _set(uid: str, t: str, a: dict[str, Any], val: str) -> None:
            await store.set_tool_cache(
                uid, t, a, val, ttl_sec=settings.cache_ttl_sec
            )

        n = len(exec_steps)
        for i, (tname, args) in enumerate(exec_steps, start=1):
            _pl(
                "[pipeline] 4a. шаг инструмента %d/%d tool=%s arguments=%s",
                i,
                n,
                tname,
                json.dumps(args, sort_keys=True, ensure_ascii=False),
            )
            chunk = await execute_tool(
                settings,
                user_id,
                tname,
                args,
                get_cached=_get,
                set_cached=_set,
                cache_ttl_sec=settings.cache_ttl_sec,
            )
            raw_chunks.append(chunk)
        tool_result_full = _format_tool_results_for_context(exec_steps, raw_chunks)

    tool_result_trunc = _truncate(tool_result_full, 4000)
    if not tool_result_full.strip() and exec_steps:
        tool_result_trunc = "(пустой вывод инструмента)"

    # think=false + все вызовы без ошибки rates.py + есть вывод — без второго LLM
    all_chunks_ok = (
        all(not _tool_output_is_rates_error(c) for c in raw_chunks)
        if exec_steps
        else False
    )
    any_chunk_nonempty = any(c.strip() for c in raw_chunks) if exec_steps else False
    bypass_responder = (
        not plan.think
        and len(exec_steps) == 1
        and bool(exec_steps)
        and all_chunks_ok
        and any_chunk_nonempty
    )
    if bypass_responder:
        reply = tool_result_full.strip()
        _pl(
            "[pipeline] 5. responder пропущен (think=false): ответ = полный вывод инструмента, len=%d",
            len(reply),
        )
        _pl(
            "[pipeline] 6. ответ пользователю (без LLM): %s",
            clip_text(reply, 20_000),
        )
        await store.append_exchange(
            user_id,
            message,
            reply or "(пусто)",
            session_ttl_sec=settings.session_ttl_sec,
            max_pairs=max(1, settings.max_history_messages // 2),
        )
        return reply, None, None

    multi_tool = len(exec_steps) > 1
    effective_think = plan.think or multi_tool
    responder_system = build_responder_system(
        extra_env_system=extra,
        think=effective_think,
        multi_tool=multi_tool,
    )
    resp_messages: list[dict[str, str]] = [
        {"role": "system", "content": responder_system},
    ]
    for m in history[-8:]:
        resp_messages.append(dict(m))
    resp_messages.append({"role": "user", "content": message})
    if tool_result_trunc:
        if len(exec_steps) == 1:
            hdr = f"Результат инструмента ({exec_steps[0][0]}):"
        elif len(exec_steps) > 1:
            hdr = (
                "Ниже выводы нескольких команд из кеша. Сформулируй один ответ пользователю по его запросу, "
                "опираясь только на эти данные:"
            )
        else:
            hdr = "Результат инструмента:"
        tool_line = f"{hdr}\n{tool_result_trunc}"
    else:
        tool_line = "Инструмент не вызывался."
    resp_messages.append({"role": "user", "content": tool_line})

    _pl(
        "[pipeline] 5. контекст для LLM (responder), модель=%s:\n%s",
        llm.responder_model_name(),
        messages_for_log(resp_messages, max_total=settings.log_llm_messages_max),
    )

    try:
        reply = (await llm.respond(resp_messages)).strip()
    except Exception as e:
        logger.exception("responder LLM failed: %s", e)
        return "", f"Ошибка модели ответа: {e}", None

    _pl(
        "[pipeline] 6. ответ пользователю (responder): %s",
        clip_text(reply, 20_000),
    )

    reply_parse_mode: Optional[str] = "html"
    if not reply and tool_result_trunc and not tool_result_trunc.startswith(
        "[rates.py код"
    ):
        reply = tool_result_trunc[:3900]
        reply_parse_mode = None

    await store.append_exchange(
        user_id,
        message,
        reply or "(пусто)",
        session_ttl_sec=settings.session_ttl_sec,
        max_pairs=max(1, settings.max_history_messages // 2),
    )

    return reply or "", None, reply_parse_mode
