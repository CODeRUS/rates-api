# -*- coding: utf-8 -*-
"""
Каталог инструментов для planner (сжатая выжимка; полная правда — USAGE.md / USAGE-AGENT.md).
Имена инструментов должны совпадать с whitelist в services/tools.py.
"""
from __future__ import annotations


def _planner_cash_cities_subsnippet() -> str:
    """Строка для вставки в описание `get_cash_report`; порядок = ``cash_report._CASH_LOCATIONS``."""
    from cash_report import _CASH_LOCATIONS

    lines = "\n".join(f"  {i}. {t[0]} — `city_n`: {i}" for i, t in enumerate(_CASH_LOCATIONS, start=1))
    return (
        "  Номера городов (как `rates.py cash` без аргументов):\n"
        f"{lines}\n"
        "  Однозначный город из реплики → сразу **`city_n`**, один вызов; **не** делай отдельный шаг только за списком. "
        "`city_name` — нестандартное название или сомнение.\n\n"
    )


# Все команды rates.py — только с --readonly (кеш, без сети).
PLANNER_TOOLS_SNIPPET = (
    """
Общие правила:
- `think=false` для одного однозначного отчёта без интерпретации; `think=true` для выбора/сравнения/пояснения и всегда при `tool_steps`.
- Формулировки «где выгоднее», «самый выгодный», «в каких отделениях/филиалах» => `think=true`.
- Все `arguments` только из текущего сообщения пользователя.
- Если параметров нет, используй `arguments: {{}}` и дефолты CLI.

Инструменты:
- `none`: только уточнение в теме курсов (без вызова rates.py). Для нерелевантной темы используй `out_of_scope=true`.

- `get_rates_summary` (`rates.py --readonly`):
  `arguments`: `output_filter?` (пусто | "travelask" | "ta").
  Общие запросы «курс», «курс валюты», «какой курс», «курс бата» => именно этот инструмент с `arguments: {{}}`.

- `get_usdt_report` (`rates.py --readonly usdt`):
  `arguments`: `{{}}`.

- `get_rshb_report` (`rates.py --readonly rshb`):
  `arguments`: `thb_amounts?`, `atm_fee?`.
  «рсхб / курсы рсхб» без чисел в текущей реплике => `arguments: {{}}` (дефолт: 30000, комиссия 250).

- `get_cash_report` (`rates.py --readonly cash ...`):
"""
    + _planner_cash_cities_subsnippet()
    + """  `arguments`: `city_n?` (предпочитай), `city_name?` (если `city_n` нельзя определить), `cash_fiat`/`fiat?` ("USD"|"EUR"|"CNY"), `source?` ("banki"|"vbr"|"rbc"|"all"), `top_n?` (<=100).
  Если город однозначен, не делай промежуточный шаг «список -> отчёт» — сразу один вызов.
  Если спрашивают про отделения/филиалы/где выгоднее по наличным, ставь `top_n: 100`.

- `get_exchange_report` (`rates.py --readonly exchange`):
  `arguments`: `exchange_fiat`/`fiat?` ("USD"|"EUR"|"CNY"), `top_n?` (<=100).
  Запросы про TT Exchange и «в каких отделениях / где выгоднее менять USD/EUR/CNY» идут сюда (не в `get_cash_report`).
  Для широкого списка отделений ставь `top_n: 100`.

- `get_calc_comparison` (`rates.py --readonly calc <budget_rub> usd|eur|cny <rub_per_fiat>`):
  Обязательные поля: `budget_rub` (int), `fiat` строго `"usd"|"eur"|"cny"`, `rub_per_fiat` (>0).
  Если этих трёх параметров нет в текущей реплике, не вызывай calc.

Если кеш пустой, это видно в stderr от rates.py; цифры не выдумывай.
"""
).strip()

# Имена, разрешённые в executor (включая ветку без вызова при needs_tool=false).
REGISTERED_TOOL_NAMES = frozenset(
    {
        "none",
        "get_rates_summary",
        "get_usdt_report",
        "get_rshb_report",
        "get_cash_report",
        "get_exchange_report",
        "get_calc_comparison",
    }
)
