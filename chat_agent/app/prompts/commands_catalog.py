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


PLANNER_TOOLS_SNIPPET = (
    """
Общие правила:
- По умолчанию `think=false` — запрос считается прямым отчётом без интерпретации; инструмент сам возвращает готовый список/таблицу.
- `think=true` только при явных словах сравнения/выбора/пояснения («где выгоднее», «самый выгодный», «сравни», «что лучше», «поясни», «почему»), и всегда при `tool_steps`.
- Сами по себе «курсы доллара/евро/юаня», «курсы в москве/спб», «в отделениях» => прямой отчёт, `think=false`.
- Все `arguments` только из текущего сообщения пользователя.
- Если параметров нет, используй `arguments: {{}}` и дефолты CLI.

Инструменты:
- `none`: только уточнение в теме курсов (без вызова rates.py). Для нерелевантной темы используй `out_of_scope=true`.

- `get_rates_summary` (`rates.py`):
  `arguments`: `receiving_thb?` (int > 0).
  Общие запросы «курс», «курс валюты», «какой курс», «курс бата», «курсы обмена», «курсы валют», «обменные курсы», «курс обмена валют» => именно этот инструмент с `arguments: {{}}` и `think=false`.
  Если в реплике нет ни города, ни валюты, ни суммы — это generic-фраза для сводки, ни в коем случае не выбирай `get_cash_report` или `get_exchange_report`.
  Запросы вида «сколько рублей нужно для получения N бат/THB» => `get_rates_summary` с `arguments: {"receiving_thb": N}` и `think=false` (однозначный прямой отчёт).
  Если в реплике есть сумма рублей, а не бат, то никогда не выбирай этот инструмент, то нужен `think=true`
  Если в реплике есть вопрос о том «сколько получу бат с N рублей», то нужен `think=true`

- `get_usdt_report` (`rates.py usdt`):
  `arguments`: `{{}}`.

- `get_rshb_report` (`rates.py rshb`):
  `arguments`: `thb_amounts?`, `atm_fee?`.
  «рсхб / курсы рсхб» без чисел в текущей реплике => `arguments: {{}}` (дефолт: 30000, комиссия 250).

- `get_cash_report` (`rates.py cash ...`):
"""
    + _planner_cash_cities_subsnippet()
    + """  `arguments`: `city_n?` (предпочитай), `city_name?` (если `city_n` нельзя определить), `cash_fiat`/`fiat?` ("USD"|"EUR"|"CNY"), `source?` ("banki"|"vbr"|"rbc"|"all"), `top_n?` (<=100).
  Если город однозначен, не делай промежуточный шаг «список -> отчёт» — сразу один вызов.
  По умолчанию `think=false` — это прямой отчёт со списком банков.
  `top_n: 100` ставь только если пользователь явно просит «все/полный список/все отделения». Для обычных «курсы доллара в <городе>» — без `top_n`.
  `think=true` только при явном сравнении/пояснении («где выгоднее», «сравни», «поясни»).

- `get_exchange_report` (`rates.py exchange`):
  `arguments`: `exchange_fiat`/`fiat?` ("USD"|"EUR"|"CNY"), `top_n?` (<=100).
  Запросы про TT Exchange и «в каких отделениях / где выгоднее менять USD/EUR/CNY» идут сюда (не в `get_cash_report`).
  По умолчанию `think=false` — это прямой отчёт.
  `think=true` только при явном сравнении/пояснении; `top_n: 100` — только если пользователь явно просит полный список.

- `get_calc_comparison` (`rates.py calc <budget_rub> usd|eur|cny <rub_per_fiat>`):
  Обязательные поля: `budget_rub` (int), `fiat` строго `"usd"|"eur"|"cny"`, `rub_per_fiat` (>0).
  Если в текущей реплике есть все три параметра — вызывай calc и ставь `think=false` (однозначный прямой отчёт).
  Calc follow-up: если в текущей реплике есть только курс (`доллар/евро/юань по N`, `N руб за 1 usd/eur/cny`), а бюджет есть в прошлой user-реплике из переданного контекста planner — вызывай calc, подставь `budget_rub` из последней такой реплики, `think=false`.
  Если не удаётся собрать все три параметра ни из текущей реплики, ни из разрешённого user-контекста planner — calc не вызывай.

- `get_avosend_report` (`rates.py avosend <mode> <amount>`):
  `arguments`: `mode` ("cash"|"bank"|"card"), `amount` (int > 0).
  Запросы про Avosend, «получение в отделении/наличными», «avosend cash 5000» веди сюда.
  Для вопросов с «что выгоднее/поясни» ставь `think=true`; для короткого запроса конкретного тарифа обычно тоже `think=true`, чтобы ответ пояснил сумму и курс.

- `get_koronapay_report` (`rates.py korona query ...`):
  `arguments`: ровно одно из `sending_rub` или `receiving_thb`; опционально `payment`, `receiving`, `raw`.
  Запросы про KoronaPay/Корона («по курсу короны», «сколько получу») веди сюда.

- `get_ex24_report` (`rates.py ex24 [amount_rub]`):
  `arguments`: `amount_rub?` (int).
  Запросы про Ex24 и курс/получение по конкретной сумме RUB.

- `get_kwikpay_report` (`rates.py kwikpay ...`):
  `arguments`: `amounts?` (list[int]), `country?`, `currency?`.
  Запросы про KwikPay/квикпей.

- `get_askmoney_report` (`rates.py askmoney [rub]`):
  `arguments`: `rub?` (int).
  Запросы про askmoney/аскмани по конкретной сумме.

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
        "get_avosend_report",
        "get_koronapay_report",
        "get_ex24_report",
        "get_kwikpay_report",
        "get_askmoney_report",
    }
)
