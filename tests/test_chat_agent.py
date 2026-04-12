# -*- coding: utf-8 -*-
"""Unit-тесты chat_agent (без Redis / LLM)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from chat_agent.app.schemas.chat import ChatRequest, PlannerOutput
from chat_agent.app.services.orchestrator import (
    _execution_steps,
    _parse_planner_output,
    _strip_json_fence,
)
from chat_agent.app.prompts.commands_catalog import PLANNER_TOOLS_SNIPPET
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
        '{"tool":"get_usdt_report","arguments":{},"needs_tool":true,"think":false}'
    )
    assert p.tool == "get_usdt_report"
    assert p.needs_tool is True
    assert p.think is False


def test_parse_planner_with_fence() -> None:
    p = _parse_planner_output(
        '```\n{"tool":"none","arguments":{},"needs_tool":false}\n```'
    )
    assert p.tool == "none"
    assert p.needs_tool is False
    assert p.think is False


def test_parse_planner_think_true() -> None:
    p = _parse_planner_output(
        '{"tool":"get_calc_comparison","arguments":{"budget_rub":100000,"fiat":"usd","rub_per_fiat":90.5},"needs_tool":true,"think":true}'
    )
    assert p.think is True


def test_planner_snippet_includes_cash_cities_from_cash_report() -> None:
    assert "1. Москва" in PLANNER_TOOLS_SNIPPET
    assert "`city_n`: 1" in PLANNER_TOOLS_SNIPPET


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
        '{"tool":"get_cash_report","arguments":{},"needs_tool":true,"think":true,'
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
            tool_steps=steps,
        )
