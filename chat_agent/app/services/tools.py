# -*- coding: utf-8 -*-
"""Whitelist инструментов: только фиксированные вызовы rates.py --readonly …"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

from chat_agent.app.config import Settings

_log = logging.getLogger(__name__)


class RatesSummaryArgs(BaseModel):
    output_filter: Optional[str] = None

    @field_validator("output_filter", mode="before")
    @classmethod
    def norm_filter(cls, v: Any) -> Optional[str]:
        if v is None or v == "":
            return None
        s = str(v).strip().lower()
        if s in ("travelask", "ta"):
            return s
        raise ValueError('output_filter must be "travelask", "ta" or omitted')


class RshbArgs(BaseModel):
    thb_amounts: Optional[list[float]] = None
    atm_fee: Optional[float] = None


class CashArgs(BaseModel):
    city_n: Optional[int] = None
    city_name: Optional[str] = None
    source: Optional[Literal["banki", "vbr", "rbc", "all"]] = None
    top_n: Optional[int] = Field(default=None, ge=1, le=100)
    #: Только USD / EUR / CNY; вместе с городом → `rates.py cash N … --fiat …`
    #: Алиас `fiat` — на случай если планировщик подставит как у calc.
    cash_fiat: Optional[Literal["USD", "EUR", "CNY"]] = Field(
        default=None,
        validation_alias=AliasChoices("cash_fiat", "fiat"),
    )

    @field_validator("city_name", mode="before")
    @classmethod
    def _strip_city_name(cls, v: Any) -> Optional[str]:
        if v is None or v == "":
            return None
        s = str(v).strip()
        return s or None

    @field_validator("cash_fiat", mode="before")
    @classmethod
    def _norm_cash_fiat(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        u = str(v).strip().upper()
        if u not in ("USD", "EUR", "CNY"):
            raise ValueError('cash_fiat must be "USD", "EUR" or "CNY"')
        return u

    @model_validator(mode="after")
    def _cash_fiat_needs_city(self) -> CashArgs:
        if self.cash_fiat and self.city_n is None and self.city_name is None:
            raise ValueError("cash_fiat требует city_n или city_name")
        return self


_CASH_MENU_LINE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


def _norm_city_token(s: str) -> str:
    return s.strip().lower().replace("ё", "е")


def _strip_city_query_prefix(q: str) -> str:
    q = q.strip()
    low = q.lower()
    for prefix in ("в ", "г. ", "город "):
        if low.startswith(prefix):
            return q[len(prefix) :].strip()
    return q


def _parse_cash_city_menu(stdout: str) -> dict[int, str]:
    lines = stdout.splitlines()
    start = 0
    for i, line in enumerate(lines):
        if "Доступные города:" in line:
            start = i + 1
            break
    out: dict[int, str] = {}
    for line in lines[start:]:
        m = _CASH_MENU_LINE.match(line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def _common_prefix_len(a: str, b: str) -> int:
    n = 0
    for ca, cb in zip(a, b):
        if ca != cb:
            break
        n += 1
    return n


def _match_city_name_to_n(name: str, menu: dict[int, str]) -> Optional[int]:
    raw = _strip_city_query_prefix(name)
    q = _norm_city_token(raw)
    if not q:
        return None

    for idx, label in sorted(menu.items()):
        if _norm_city_token(label) == q:
            return idx

    candidates: list[int] = []
    for idx, label in sorted(menu.items()):
        lab = _norm_city_token(label)
        if q in lab or lab in q:
            candidates.append(idx)
            continue
        if len(q) >= 4 and len(lab) >= 4 and _common_prefix_len(q, lab) >= 4:
            candidates.append(idx)
            continue
        if len(q) >= 3 and (lab.startswith(q) or q.startswith(lab)):
            candidates.append(idx)

    uniq = list(dict.fromkeys(candidates))
    if len(uniq) == 1:
        return uniq[0]
    return None


class ExchangeArgs(BaseModel):
    top_n: int = Field(default=10, ge=1, le=100)
    exchange_fiat: Optional[Literal["USD", "EUR", "CNY"]] = Field(
        default=None,
        validation_alias=AliasChoices("exchange_fiat", "fiat"),
    )

    @field_validator("exchange_fiat", mode="before")
    @classmethod
    def _norm_exchange_fiat(cls, v: Any) -> Any:
        if v is None or v == "":
            return None
        u = str(v).strip().upper()
        if u not in ("USD", "EUR", "CNY"):
            raise ValueError('exchange_fiat must be "USD", "EUR" or "CNY"')
        return u


class CalcArgs(BaseModel):
    budget_rub: int = Field(..., ge=1)
    fiat: Literal["usd", "eur", "cny"]
    rub_per_fiat: float = Field(..., gt=0)

    @field_validator("fiat", mode="before")
    @classmethod
    def _norm_calc_fiat(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and not v.strip()):
            return v
        s = str(v).strip().lower()
        if s == "thb":
            raise ValueError(
                'calc: fiat только usd, eur или cny (промежуточная валюта), не thb. '
                "Нужен также rub_per_fiat — ₽ за 1 ед. этой валюты. Иначе get_rates_summary, не calc."
            )
        return s


class AvosendArgs(BaseModel):
    mode: Literal["cash", "bank", "card"] = Field(default="cash")
    amount: int = Field(..., ge=1)

    @field_validator("mode", mode="before")
    @classmethod
    def _norm_mode(cls, v: Any) -> Any:
        if v is None or v == "":
            return "cash"
        return str(v).strip().lower()


class KoronapayArgs(BaseModel):
    sending_rub: Optional[int] = Field(default=None, ge=1)
    receiving_thb: Optional[int] = Field(default=None, ge=1)
    payment: Optional[str] = None
    receiving: Optional[str] = None
    raw: bool = False

    @model_validator(mode="after")
    def _one_amount(self) -> KoronapayArgs:
        if (self.sending_rub is None) == (self.receiving_thb is None):
            raise ValueError("укажите ровно одно из: sending_rub или receiving_thb")
        return self


class Ex24Args(BaseModel):
    amount_rub: Optional[int] = Field(default=None, ge=1)


class KwikpayArgs(BaseModel):
    amounts: Optional[list[int]] = None
    country: Optional[str] = None
    currency: Optional[str] = None


class AskmoneyArgs(BaseModel):
    rub: Optional[int] = Field(default=None, ge=1)


async def _run_rates(
    settings: Settings,
    tail_after_readonly: list[str],
) -> str:
    repo = settings.repo_root.resolve()
    script = repo / "rates.py"
    if not script.is_file():
        return f"Ошибка: не найден {script}"
    cmd = [sys.executable, str(script), "--readonly", *tail_after_readonly]
    if settings.pipeline_log:
        _log.info("[pipeline] запуск rates.py: %s", shlex.join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(repo),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=None,
    )
    try:
        out_b, err_b = await asyncio.wait_for(
            proc.communicate(),
            timeout=settings.tool_timeout_sec,
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "Таймаут выполнения rates.py (инструмент)."
    out = (out_b or b"").decode("utf-8", errors="replace").strip()
    err = (err_b or b"").decode("utf-8", errors="replace").strip()
    code = proc.returncode or 0
    if code != 0:
        msg = "\n".join(x for x in (out, err) if x)
        return f"[rates.py код {code}] {msg}".strip()
    return out if out else err


async def tool_get_rates_summary(settings: Settings, arguments: dict[str, Any]) -> str:
    m = RatesSummaryArgs.model_validate(arguments or {})
    tail: list[str] = []
    if m.output_filter:
        tail.extend(["--filter", m.output_filter])
    return await _run_rates(settings, tail)


async def tool_get_usdt_report(settings: Settings, arguments: dict[str, Any]) -> str:
    _ = arguments
    return await _run_rates(settings, ["usdt"])


async def tool_get_rshb_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = RshbArgs.model_validate(arguments or {})
    tail = ["rshb"]
    if m.thb_amounts is not None and len(m.thb_amounts) > 0:
        for x in m.thb_amounts:
            tail.append(str(int(x)))
        fee = m.atm_fee if m.atm_fee is not None else 250.0
        tail.append(str(int(fee)))
    elif m.atm_fee is not None:
        raise ValueError("atm_fee без thb_amounts не поддерживается")
    return await _run_rates(settings, tail)


def _cash_report_argv_suffix(m: CashArgs, *, include_fiat: bool) -> list[str]:
    """Хвост argv после `cash` [N]: источник, --top, опционально --fiat."""
    out: list[str] = []
    if m.source is not None:
        out.append(m.source)
    if m.top_n is not None:
        out.extend(["--top", str(int(m.top_n))])
    if include_fiat and m.cash_fiat is not None:
        out.extend(["--fiat", m.cash_fiat])
    return out


async def tool_get_cash_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = CashArgs.model_validate(arguments or {})
    suffix = _cash_report_argv_suffix(m, include_fiat=True)

    city_n: Optional[int] = m.city_n
    if city_n is None and m.city_name:
        # Только `cash`: без N, --fiat, источника и --top (иначе CLI не печатает список городов).
        list_out = await _run_rates(settings, ["cash"])
        if list_out.startswith("[rates.py"):
            return list_out
        menu = _parse_cash_city_menu(list_out)
        if not menu:
            return (
                list_out
                + "\n\n[агент] В выводе не найден нумерованный список городов для сопоставления city_name."
            )
        resolved = _match_city_name_to_n(m.city_name, menu)
        if resolved is None:
            return (
                list_out
                + f"\n\n[агент] Не удалось однозначно сопоставить город «{m.city_name}» "
                "с номером из списка выше — укажите `city_n` или уточните название."
            )
        city_n = resolved

    tail = ["cash"]
    if city_n is not None:
        tail.append(str(int(city_n)))
    tail.extend(suffix)
    return await _run_rates(settings, tail)


async def tool_get_exchange_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = ExchangeArgs.model_validate(arguments or {})
    tail = ["exchange", "--top", str(int(m.top_n))]
    if m.exchange_fiat is not None:
        tail.extend(["--fiat", m.exchange_fiat])
    return await _run_rates(settings, tail)


async def tool_get_calc_comparison(settings: Settings, arguments: dict[str, Any]) -> str:
    m = CalcArgs.model_validate(arguments or {})
    tail = [
        "calc",
        str(int(m.budget_rub)),
        m.fiat,
        str(m.rub_per_fiat),
    ]
    return await _run_rates(settings, tail)


async def tool_get_avosend_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = AvosendArgs.model_validate(arguments or {})
    tail = ["avosend", m.mode, str(int(m.amount))]
    return await _run_rates(settings, tail)


async def tool_get_koronapay_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = KoronapayArgs.model_validate(arguments or {})
    tail = ["korona", "query"]
    if m.sending_rub is not None:
        tail.extend(["--sending-rub", str(int(m.sending_rub))])
    if m.receiving_thb is not None:
        tail.extend(["--receiving-thb", str(int(m.receiving_thb))])
    if m.payment:
        tail.extend(["--payment", m.payment.strip()])
    if m.receiving:
        tail.extend(["--receiving", m.receiving.strip()])
    if m.raw:
        tail.append("--raw")
    return await _run_rates(settings, tail)


async def tool_get_ex24_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = Ex24Args.model_validate(arguments or {})
    tail = ["ex24"]
    if m.amount_rub is not None:
        tail.append(str(int(m.amount_rub)))
    return await _run_rates(settings, tail)


async def tool_get_kwikpay_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = KwikpayArgs.model_validate(arguments or {})
    tail = ["kwikpay"]
    if m.amounts:
        tail.extend(["--amounts", ",".join(str(int(x)) for x in m.amounts)])
    if m.country:
        tail.extend(["--country", m.country.strip()])
    if m.currency:
        tail.extend(["--currency", m.currency.strip()])
    return await _run_rates(settings, tail)


async def tool_get_askmoney_report(settings: Settings, arguments: dict[str, Any]) -> str:
    m = AskmoneyArgs.model_validate(arguments or {})
    tail = ["askmoney"]
    if m.rub is not None:
        tail.append(str(int(m.rub)))
    return await _run_rates(settings, tail)


ToolFn = Callable[[Settings, dict[str, Any]], Coroutine[Any, Any, str]]

TOOL_HANDLERS: Dict[str, ToolFn] = {
    "get_rates_summary": tool_get_rates_summary,
    "get_usdt_report": tool_get_usdt_report,
    "get_rshb_report": tool_get_rshb_report,
    "get_cash_report": tool_get_cash_report,
    "get_exchange_report": tool_get_exchange_report,
    "get_calc_comparison": tool_get_calc_comparison,
    "get_avosend_report": tool_get_avosend_report,
    "get_koronapay_report": tool_get_koronapay_report,
    "get_ex24_report": tool_get_ex24_report,
    "get_kwikpay_report": tool_get_kwikpay_report,
    "get_askmoney_report": tool_get_askmoney_report,
}


async def execute_tool(
    settings: Settings,
    user_id: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    get_cached: Callable[[str, str, dict[str, Any]], Coroutine[Any, Any, Optional[str]]],
    set_cached: Callable[[str, str, dict[str, Any], str], Coroutine[Any, Any, None]],
    cache_ttl_sec: int,
) -> str:
    if tool == "none":
        return ""
    if tool not in TOOL_HANDLERS:
        return f"Инструмент {tool!r} не разрешён."
    args_compact = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    cached = await get_cached(user_id, tool, arguments)
    if cached is not None:
        if settings.pipeline_log:
            _log.info(
                "[pipeline] инструмент: кеш HIT tool=%s user_id=%s arguments=%s len_out=%d",
                tool,
                user_id,
                args_compact,
                len(cached),
            )
        return cached
    if settings.pipeline_log:
        _log.info(
            "[pipeline] инструмент: кеш MISS, запуск rates.py tool=%s user_id=%s arguments=%s",
            tool,
            user_id,
            args_compact,
        )
    try:
        result = await TOOL_HANDLERS[tool](settings, arguments)
    except Exception as e:
        if settings.pipeline_log:
            _log.info("[pipeline] инструмент: исключение tool=%s err=%s", tool, e)
        return f"Ошибка инструмента {tool}: {e}"
    max_log = settings.log_tool_output_max
    if max_log <= 0:
        out_for_log = result
    elif len(result) <= max_log:
        out_for_log = result
    else:
        out_for_log = result[:max_log] + f"\n… [лог обрезан, всего символов: {len(result)}]"
    if settings.pipeline_log:
        _log.info(
            "[pipeline] инструмент: вывод rates.py tool=%s len=%d\n%s",
            tool,
            len(result),
            out_for_log,
        )
    if not result.startswith("[rates.py код"):
        await set_cached(user_id, tool, arguments, result)
    return result
