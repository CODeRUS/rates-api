# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional

from chat_agent.app.config import Settings
from chat_agent.app.prompts.planner_system import build_planner_system, build_responder_system
from chat_agent.app.schemas.chat import PlannerOutput
from chat_agent.app.services.llm.base import LLMRequestUsage, LLMUsage
from chat_agent.app.services.llm_client import LLMClient
from chat_agent.app.services.redis_store import RedisStore
from chat_agent.app.pipeline_log import clip_text, messages_for_log
from chat_agent.app.prompts.commands_catalog import REGISTERED_TOOL_NAMES
from chat_agent.app.services.tools import execute_tool

logger = logging.getLogger(__name__)

# Сообщения без вызова responder: только тема курсов / обмена из rates.py.
_MSG_OUT_OF_SCOPE = (
    "Я отвечаю только на вопросы про курсы и обмен: сводка RUB→THB, наличные в городах России, "
    "обменники TT Exchange, USDT, карты РСХБ/UnionPay, сравнение через USD/EUR/CNY (calc). "
    "Ваш запрос к этой теме не относится — переформулируйте, пожалуйста."
)
_MSG_UNRECOGNIZED_PLAN = (
    "Не удалось сопоставить запрос с доступными командами. "
    "Напишите, что нужно из курсов: сводка, наличные (город), обмен TT, USDT, РСХБ или calc с суммой и курсом."
)

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


def _early_fixed_reply_for_plan(
    plan: PlannerOutput, exec_steps: list[tuple[str, dict[str, Any]]]
) -> Optional[str]:
    """
    Если инструменты не запускаем — готовый текст пользователю (без второго LLM).
    ``exec_steps`` уже нормализован: не None (ошибка whitelist обрабатывается выше).
    """
    if exec_steps:
        return None
    if plan.out_of_scope:
        return _MSG_OUT_OF_SCOPE
    if plan.needs_tool:
        return _MSG_UNRECOGNIZED_PLAN
    return None


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


def _planner_history_suffix(
    history: list[dict[str, str]], *, max_user_turns: int
) -> list[dict[str, str]]:
    """
    Непрерывный хвост истории (старые → новые), охватывающий последние ``max_user_turns`` user-сообщений.
    Включает ответы assistant между ними. ``max_user_turns`` 0 → пусто.
    """
    if max_user_turns <= 0 or not history:
        return []
    n_users = 0
    start = len(history)
    for i in range(len(history) - 1, -1, -1):
        if history[i].get("role") == "user":
            n_users += 1
            start = i
            if n_users >= max_user_turns:
                break
    return [dict(m) for m in history[start:]]


def _compress_planner_chat_messages(
    messages: list[dict[str, str]], *, max_chars: int
) -> list[dict[str, str]]:
    """Укорачивает content каждого сообщения для экономии токенов (0 = без усечения)."""
    out: list[dict[str, str]] = []
    for m in messages:
        role = str(m.get("role", "user"))
        if role not in ("user", "assistant"):
            role = "user"
        content = str(m.get("content", ""))
        if max_chars > 0 and len(content) > max_chars:
            content = (
                content[: max(0, max_chars - 40)].rstrip()
                + "\n… [усечено для planner]"
            )
        out.append({"role": role, "content": content})
    return out


def _drop_trailing_user(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Удаляет последний user-хвост, чтобы текущая реплика не дублировалась подряд."""
    if not messages:
        return []
    out = [dict(m) for m in messages]
    if str(out[-1].get("role", "")) == "user":
        out.pop()
    return out


class _LLMTokenAccumulator:
    """Сумма usage по всем вызовам planner/responder за один HTTP-запрос /chat."""

    __slots__ = ("prompt", "completion", "total", "calls")

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0
        self.total = 0
        self.calls = 0

    def add(self, u: LLMUsage) -> None:
        if u.prompt_tokens is not None:
            self.prompt += int(u.prompt_tokens)
        if u.completion_tokens is not None:
            self.completion += int(u.completion_tokens)
        if u.total_tokens is not None:
            self.total += int(u.total_tokens)
        self.calls += 1


def _log_llm_tokens_for_request(acc: _LLMTokenAccumulator, *, user_id: str) -> None:
    """Одна строка на запрос /chat; не зависит от CHAT_AGENT_PIPELINE_LOG."""
    if acc.calls == 0:
        return
    logger.info(
        "[pipeline] 7. LLM токены за запрос user_id=%s: prompt=%d completion=%d total=%d (вызовов_LLM=%d)",
        user_id,
        acc.prompt,
        acc.completion,
        acc.total,
        acc.calls,
    )


def _request_usage_from_acc(acc: _LLMTokenAccumulator) -> LLMRequestUsage:
    return LLMRequestUsage(
        prompt_tokens=acc.prompt,
        completion_tokens=acc.completion,
        total_tokens=acc.total,
        calls=acc.calls,
    )


async def run_chat_turn(
    *,
    settings: Settings,
    store: RedisStore,
    llm: LLMClient,
    user_id: str,
    message: str,
    include_env_system: bool,
    on_responder_chunk: Optional[Callable[[str], Awaitable[None]]] = None,
) -> tuple[str, Optional[str], Optional[str], LLMRequestUsage]:
    """
    Возвращает (reply, error, reply_parse_mode, llm_request_usage).
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
        return "", "Слишком много запросов. Подождите минуту.", None, LLMRequestUsage()

    token_acc = _LLMTokenAccumulator()

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
    hist_suffix = _planner_history_suffix(
        history, max_user_turns=settings.planner_user_history_turns
    )
    hist_suffix = _drop_trailing_user(hist_suffix)
    hist_compressed = _compress_planner_chat_messages(
        hist_suffix, max_chars=settings.planner_history_message_max_chars
    )
    _pl(
        "[pipeline] 1d. в planner передано сообщений сжатой истории: %d (суффикс под %d user-реплик, CHAT_AGENT_PLANNER_HISTORY_MSG_MAX=%d)",
        len(hist_compressed),
        settings.planner_user_history_turns,
        settings.planner_history_message_max_chars,
    )
    planner_messages.extend(hist_compressed)
    final_user = (message or "").strip()
    if hist_compressed:
        final_user = (
            "Текущая реплика пользователя (только под неё строй JSON-план):\n" + final_user
        )
    planner_messages.append({"role": "user", "content": final_user})

    _pl(
        "[pipeline] 2. контекст для LLM (planner), модель=%s:\n%s",
        llm.planner_model_name(),
        messages_for_log(planner_messages, max_total=settings.log_llm_messages_max),
    )

    plan_comp = await llm.plan(planner_messages)
    token_acc.add(plan_comp.usage)
    raw_plan = plan_comp.text
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
                    "Исправь ответ: верни только один JSON-объект с ключами tool, arguments, needs_tool, think, "
                    "out_of_scope (обязательный bool: true если вопрос не про курсы/обмен из каталога, иначе false) "
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
            plan_retry = await llm.plan(fix_messages)
            token_acc.add(plan_retry.usage)
            raw_plan = plan_retry.text
            plan = _parse_planner_output(raw_plan)
        except Exception as e2:
            logger.exception("planner retry failed: %s", e2)
            await store.append_exchange(
                user_id,
                message,
                _MSG_UNRECOGNIZED_PLAN,
                session_ttl_sec=settings.session_ttl_sec,
                max_pairs=max(1, settings.max_history_messages // 2),
            )
            _log_llm_tokens_for_request(token_acc, user_id=user_id)
            return _MSG_UNRECOGNIZED_PLAN, None, None, _request_usage_from_acc(token_acc)

    _pl(
        "[pipeline] 3. ответ planner (сырой JSON от модели): %s",
        clip_text(raw_plan or "(пусто)", 20_000),
    )

    exec_steps = _execution_steps(plan)
    if exec_steps is None:
        _pl(
            "[pipeline] 3b. tool/tool_steps вне whitelist — фиксированный ответ tool=%s",
            plan.tool,
        )
        await store.append_exchange(
            user_id,
            message,
            _MSG_UNRECOGNIZED_PLAN,
            session_ttl_sec=settings.session_ttl_sec,
            max_pairs=max(1, settings.max_history_messages // 2),
        )
        _log_llm_tokens_for_request(token_acc, user_id=user_id)
        return _MSG_UNRECOGNIZED_PLAN, None, None, _request_usage_from_acc(token_acc)

    early_reply = _early_fixed_reply_for_plan(plan, exec_steps)
    if early_reply is not None:
        _pl(
            "[pipeline] 3c. ранний ответ без инструментов и без responder (%s)",
            "out_of_scope" if plan.out_of_scope else "needs_tool_without_steps",
        )
        await store.append_exchange(
            user_id,
            message,
            early_reply,
            session_ttl_sec=settings.session_ttl_sec,
            max_pairs=max(1, settings.max_history_messages // 2),
        )
        _log_llm_tokens_for_request(token_acc, user_id=user_id)
        return early_reply, None, None, _request_usage_from_acc(token_acc)

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
        _log_llm_tokens_for_request(token_acc, user_id=user_id)
        return reply, None, None, _request_usage_from_acc(token_acc)

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
    responder_history = _drop_trailing_user(
        history[-settings.responder_history_messages :]
    )
    for m in responder_history:
        resp_messages.append(dict(m))
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
        tool_ctx = f"{hdr}\n{tool_result_trunc}"
    else:
        tool_ctx = (
            "В **этом** запросе rates.py не вызывали — отдельного блока с выводом скрипта ниже нет. "
            "Это не значит, что кеш сервера пуст. Если в истории диалога выше уже есть ответ ассистента с курсами — "
            "разворачивай ответ **только** на основе этих цифр; не говори «данных в кеше нет». "
            "Не придумывай новые каналы и курсы вне уже показанного."
        )
    # Одно user-сообщение: иначе модель воспринимает второй блок как ещё одну реплику пользователя
    # (в логах это выглядело как три подряд user без assistant).
    resp_messages.append(
        {
            "role": "user",
            "content": (
                "Реплика пользователя (отвечай по смыслу только на неё, в рамках системной инструкции):\n"
                f"{message}\n\n"
                "Служебный контекст backend (это НЕ текст пользователя):\n"
                f"{tool_ctx}"
            ),
        }
    )

    _pl(
        "[pipeline] 5. контекст для LLM (responder), модель=%s:\n%s",
        llm.responder_model_name(),
        messages_for_log(resp_messages, max_total=settings.log_llm_messages_max),
    )

    try:
        if on_responder_chunk is not None:
            parts: list[str] = []
            async for ch in llm.respond_stream(resp_messages, on_usage=token_acc.add):
                if not ch:
                    continue
                parts.append(ch)
                await on_responder_chunk(ch)
            reply = "".join(parts).strip()
        else:
            resp_comp = await llm.respond(resp_messages)
            token_acc.add(resp_comp.usage)
            reply = resp_comp.text.strip()
    except Exception as e:
        logger.exception("responder LLM failed: %s", e)
        _log_llm_tokens_for_request(token_acc, user_id=user_id)
        return "", f"Ошибка модели ответа: {e}", None, _request_usage_from_acc(token_acc)

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

    _log_llm_tokens_for_request(token_acc, user_id=user_id)
    return reply or "", None, reply_parse_mode, _request_usage_from_acc(token_acc)
