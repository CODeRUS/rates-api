# -*- coding: utf-8 -*-
"""Unit-тесты chat_agent (без Redis / LLM)."""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from chat_agent.app.schemas.chat import ChatRequest, PlannerOutput
from chat_agent.app.services.orchestrator import (
    _early_fixed_reply_for_plan,
    _compress_planner_chat_messages,
    _execution_steps,
    _parse_planner_output,
    _planner_history_suffix,
    _strip_json_fence,
)
from chat_agent.app.prompts.commands_catalog import PLANNER_TOOLS_SNIPPET
from chat_agent.app.prompts.planner_system import build_planner_system
from chat_agent.app.services.tools import (
    CashArgs,
    _cash_report_argv_suffix,
    _match_city_name_to_n,
    _parse_cash_city_menu,
)


def test_chat_request_valid_user_id() -> None:
    r = ChatRequest(user_id="12345", message="привет")
    assert r.user_id == "12345"


def test_chat_request_invalid_user_id() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(user_id="abc", message="x")


def test_strip_json_fence() -> None:
    raw = '```json\n{"tool":"none","arguments":{},"needs_tool":false}\n```'
    assert '"tool"' in _strip_json_fence(raw)


def test_parse_planner_output() -> None:
    p = _parse_planner_output(
        '{"tool":"get_usdt_report","arguments":{},"needs_tool":true,"think":false,"out_of_scope":false}'
    )
    assert p.tool == "get_usdt_report"
    assert p.needs_tool is True
    assert p.think is False


def test_parse_planner_with_fence() -> None:
    p = _parse_planner_output(
        '```\n{"tool":"none","arguments":{},"needs_tool":false,"think":false,"out_of_scope":false}\n```'
    )
    assert p.tool == "none"
    assert p.needs_tool is False
    assert p.think is False


def test_parse_planner_think_true() -> None:
    p = _parse_planner_output(
        '{"tool":"get_calc_comparison","arguments":{"budget_rub":100000,"fiat":"usd","rub_per_fiat":90.5},'
        '"needs_tool":true,"think":true,"out_of_scope":false}'
    )
    assert p.think is True


def test_parse_planner_out_of_scope() -> None:
    p = _parse_planner_output(
        '{"tool":"none","arguments":{},"needs_tool":false,"think":false,"out_of_scope":true}'
    )
    assert p.out_of_scope is True


def test_early_fixed_reply_out_of_scope() -> None:
    p = PlannerOutput(
        tool="none",
        arguments={},
        needs_tool=False,
        think=False,
        out_of_scope=True,
    )
    assert _early_fixed_reply_for_plan(p, []) is not None
    assert "только на вопросы" in _early_fixed_reply_for_plan(p, [])


def test_early_fixed_reply_none_when_tools_run() -> None:
    p = PlannerOutput(
        tool="get_usdt_report",
        arguments={},
        needs_tool=True,
        think=False,
        out_of_scope=False,
    )
    assert _early_fixed_reply_for_plan(p, [("get_usdt_report", {})]) is None


def test_planner_history_suffix_covers_last_n_users() -> None:
    hist = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
    ]
    assert [m["content"] for m in _planner_history_suffix(hist, max_user_turns=1)] == [
        "u3",
        "a3",
    ]
    assert [m["content"] for m in _planner_history_suffix(hist, max_user_turns=2)] == [
        "u2",
        "a2",
        "u3",
        "a3",
    ]
    assert _planner_history_suffix(hist, max_user_turns=0) == []


def test_compress_planner_chat_messages_truncates() -> None:
    long = "x" * 100
    out = _compress_planner_chat_messages(
        [{"role": "assistant", "content": long}], max_chars=30
    )
    assert len(out[0]["content"]) < len(long)
    assert "усечено" in out[0]["content"]
    assert _compress_planner_chat_messages(
        [{"role": "user", "content": "ok"}], max_chars=0
    )[0]["content"] == "ok"


def test_planner_system_covers_followup_podrobnee_and_brand_routing() -> None:
    s = build_planner_system(extra_env_system="")
    assert "Follow-up" in s or "follow-up" in s.lower()
    assert "подробнее" in s
    assert "get_rates_summary" in s
    assert "Avosend" in s or "авосенд" in s
    assert "receiving_thb" in s


def test_early_fixed_reply_needs_tool_without_steps() -> None:
    p = PlannerOutput(
        tool="none",
        arguments={},
        needs_tool=True,
        think=True,
        out_of_scope=False,
    )
    r = _early_fixed_reply_for_plan(p, [])
    assert r is not None
    assert "Не удалось сопоставить" in r


def test_planner_snippet_includes_cash_cities_from_cash_report() -> None:
    assert "1. Москва" in PLANNER_TOOLS_SNIPPET
    assert "`city_n`: 1" in PLANNER_TOOLS_SNIPPET


def test_planner_snippet_receiving_thb_forces_think_false() -> None:
    assert 'arguments: {"receiving_thb": N}' in PLANNER_TOOLS_SNIPPET
    assert "think=false" in PLANNER_TOOLS_SNIPPET


def test_planner_snippet_lists_generic_exchange_phrases_for_summary() -> None:
    """Чтобы planner узнавал «курсы обмена» как generic-сводку, а не
    пытался звать get_cash_report со случайным городом."""
    assert "курсы обмена" in PLANNER_TOOLS_SNIPPET
    assert "обменные курсы" in PLANNER_TOOLS_SNIPPET
    assert "курсы валют" in PLANNER_TOOLS_SNIPPET


def test_build_planner_system_generic_query_forces_rates_summary() -> None:
    """Ключевое правило для planner: если в реплике нет ни города, ни валюты,
    ни суммы — ВСЕГДА `get_rates_summary` с пустыми arguments. Это решение
    описано в промпте (а не в питон-логике), потому что вариантов написания
    generic-фразы бесконечно, перечислить их в whitelist нереально.
    """
    prompt = build_planner_system(extra_env_system="")
    assert "общих/обзорных запросов про курс" in prompt
    assert "курсы обмена" in prompt
    assert "обменные курсы" in prompt
    assert "Список примерный и не исчерпывающий" in prompt
    assert (
        "если в реплике НЕТ ни конкретного города, ни конкретной валюты"
        in prompt
    )
    assert "НИКОГДА не выбирай в этих случаях `get_cash_report`" in prompt


def test_build_planner_system_receiving_thb_forces_think_false() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert 'arguments={"receiving_thb": N}' in prompt
    assert "think=false" in prompt


def test_build_planner_system_calc_forces_think_false() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "Для calc:" in prompt
    assert "get_calc_comparison" in prompt
    assert "think=false" in prompt


def test_build_planner_system_cash_report_defaults_to_think_false() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "get_cash_report" in prompt
    assert "get_exchange_report" in prompt
    assert "по умолчанию `think=false`" in prompt.lower()
    assert "курсы доллара в <городе>" in prompt


def test_planner_snippet_cash_report_no_blanket_top_n_100() -> None:
    assert (
        "Если спрашивают про отделения/филиалы/где выгоднее по наличным, ставь `top_n: 100`"
        not in PLANNER_TOOLS_SNIPPET
    )
    assert "Для широкого списка отделений ставь `top_n: 100`" not in PLANNER_TOOLS_SNIPPET


def test_build_planner_system_inherits_categorical_args_on_followup() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "Категориальные параметры" in prompt
    assert "унаследовать" in prompt
    assert "а в питере?" in prompt
    assert "а в евро?" in prompt
    assert "Числовые параметры" in prompt
    assert "НЕ наследуй" in prompt


def test_build_planner_system_cash_followup_city_inherits_fiat() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "Follow-up для `get_cash_report`" in prompt
    assert "подставь тот же `fiat`" in prompt
    assert "сохрани прошлый `city_n`/`city_name`" in prompt


def test_build_planner_system_forbids_tool_steps_on_simple_followup() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "`tool_steps`" in prompt
    assert "НИКОГДА не используй `tool_steps`" in prompt
    assert "а в казани?" in prompt
    assert "а в евро?" in prompt


def test_build_planner_system_requires_single_root_json_object() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "ОДИН JSON-объект верхнего уровня" in prompt
    assert "НЕ возвращай массив" in prompt


def test_parse_cash_city_menu() -> None:
    text = "Доступные города:\n1. Москва\n2. Санкт-Петербург\n"
    m = _parse_cash_city_menu(text)
    assert m == {1: "Москва", 2: "Санкт-Петербург"}


def test_match_city_name_moscow_declension() -> None:
    menu = {1: "Москва", 2: "Казань"}
    assert _match_city_name_to_n("москве", menu) == 1
    assert _match_city_name_to_n("в Москве", menu) == 1
    assert _match_city_name_to_n("Москва", menu) == 1


def test_match_city_name_ambiguous_returns_none() -> None:
    menu = {1: "Абакан", 2: "Абаза"}
    assert _match_city_name_to_n("аба", menu) is None


def test_parse_planner_tool_steps() -> None:
    raw = (
        '{"tool":"get_cash_report","arguments":{},"needs_tool":true,"think":true,"out_of_scope":false,'
        '"tool_steps":[{"tool":"get_cash_report","arguments":{}},'
        '{"tool":"get_cash_report","arguments":{"city_n":1}}]}'
    )
    p = _parse_planner_output(raw)
    assert p.tool_steps is not None
    assert len(p.tool_steps) == 2
    assert p.tool_steps[1].arguments.get("city_n") == 1


def test_execution_steps_prefers_tool_steps() -> None:
    p = PlannerOutput.model_validate(
        {
            "tool": "get_usdt_report",
            "arguments": {},
            "needs_tool": True,
            "think": True,
            "out_of_scope": False,
            "tool_steps": [
                {"tool": "get_cash_report", "arguments": {}},
                {"tool": "get_cash_report", "arguments": {"city_n": 2}},
            ],
        }
    )
    steps = _execution_steps(p)
    assert steps == [
        ("get_cash_report", {}),
        ("get_cash_report", {"city_n": 2}),
    ]


def test_cash_args_accepts_fiat_alias() -> None:
    m = CashArgs.model_validate({"fiat": "USD", "city_name": "москва"})
    assert m.cash_fiat == "USD"


def test_cash_report_argv_suffix_fiat_optional() -> None:
    m = CashArgs.model_validate({"city_n": 1, "cash_fiat": "EUR", "source": "banki"})
    assert _cash_report_argv_suffix(m, include_fiat=True) == ["banki", "--fiat", "EUR"]
    assert _cash_report_argv_suffix(m, include_fiat=False) == ["banki"]


def test_planner_tool_steps_too_many() -> None:
    steps = [{"tool": "get_usdt_report", "arguments": {}}] * 6
    with pytest.raises(ValidationError):
        PlannerOutput(
            tool="none",
            arguments={},
            needs_tool=True,
            think=False,
            out_of_scope=False,
            tool_steps=steps,
        )


def test_build_planner_system_final_message_is_sole_plan_target() -> None:
    """Правило 0: план только на последнюю user-реплику; история — контекст."""
    prompt = build_planner_system(extra_env_system="")
    pl = prompt.lower()
    assert "последнюю" in pl or "финальную" in pl or "текущая реплика" in pl
    assert "сжатая" in pl and "история" in pl


def test_build_planner_system_tool_steps_convention_empty_root_args() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "Конвенция при `tool_steps`" in prompt
    assert "`arguments` у root всегда `{}`" in prompt
    assert "Не дублируй" in prompt


def test_build_planner_system_forbids_carrying_tool_steps_across_turns() -> None:
    prompt = build_planner_system(extra_env_system="")
    assert "каждая реплика оценивается независимо" in prompt.lower()
    assert "даже если прошлый ход был `think=true`" in prompt

