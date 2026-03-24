#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент к бесплатному Forex/курсовому API ExchangeRate-API (open access).

Документация: https://www.exchangerate-api.com/docs/free
Эндпоинт:     GET https://open.er-api.com/v6/latest/<BASE>

Особенности:
  * API ключ не нужен.
  * Данные обновляются примерно раз в сутки; в ответе есть время следующего обновления.
  * Условия использования требуют атрибуции (ссылка на сервис) на страницах, где
    показываются курсы: https://www.exchangerate-api.com

Поддерживаются пары между RUB, THB, USD и любыми другими кодами из ответа API
(через пересчёт относительно выбранной базы).
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Sequence

# Базовый URL шаблон: подставляется ISO 4217 код (USD, RUB, THB, …)
OPEN_ER_API_LATEST = "https://open.er-api.com/v6/latest/{base}"

DEFAULT_TIMEOUT = 25
USER_AGENT = "forex-er-api-client/1.0 (python)"

# Три валюты из запроса пользователя (для удобных констант и примера матрицы)
CCY_RUB = "RUB"
CCY_THB = "THB"
CCY_USD = "USD"
FOCUS_TRIO = (CCY_RUB, CCY_THB, CCY_USD)


def _http_get_json(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET и разбор JSON (только стандартная библиотека)."""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return json.loads(resp.read().decode(charset, errors="replace"))


def fetch_latest(base: str = CCY_USD, *, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """
    Загружает последние курсы относительно базовой валюты ``base``.

    :param base: ISO 4217, например USD, RUB или THB.
    :return: Полный объект ответа API (поля ``result``, ``base_code``, ``rates``, таймстемпы и т.д.).
    :raises RuntimeError: если ``result != "success"``.
    :raises urllib.error.HTTPError: при HTTP-ошибках (в т.ч. 429 при частых запросах).
    """
    base = base.strip().upper()
    url = OPEN_ER_API_LATEST.format(base=urllib.parse.quote(base, safe=""))
    data = _http_get_json(url, timeout=timeout)
    if not isinstance(data, dict):
        raise RuntimeError("Неожиданный ответ API (не объект JSON).")
    if data.get("result") != "success":
        err = data.get("error-type", data.get("error_type", "unknown"))
        raise RuntimeError(f"Ошибка API: {err}")
    return data


def get_rates_table(base: str = CCY_USD, *, timeout: float = DEFAULT_TIMEOUT) -> Dict[str, float]:
    """
    Возвращает только словарь ``код -> курс`` (сколько единиц валюты за 1 единицу базы).

    При ``base=USD`` и курсе THB=33 это значит: 1 USD = 33 THB.
    """
    data = fetch_latest(base, timeout=timeout)
    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise RuntimeError("В ответе нет словаря rates.")
    # Приводим к float
    return {str(k).upper(): float(v) for k, v in rates.items()}


def cross_rate(
    from_currency: str,
    to_currency: str,
    *,
    base: str = CCY_USD,
    rates: Optional[Dict[str, float]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> float:
    """
    Курс пересчёта: умножьте сумму в ``from_currency`` на возвращаемое число,
    чтобы получить эквивалент в ``to_currency``.

    Логика: один запрос с базой ``base`` (по умолчанию USD). Для любых A и B из таблицы:
    ``rate(A->B) = rates[B] / rates[A]``, где ``rates[X]`` — сколько X за 1 единицу ``base``.

    Пример (base=USD): rates[THB]=33, rates[RUB]=90 → 1 RUB = 33/90 THB.

    :param from_currency: исходный код ISO 4217.
    :param to_currency: целевой код.
    :param base: база запроса к API (должна присутствовать в ответе; USD надёжнее всего).
    :param rates: если уже загружали таблицу — передайте, чтобы не дергать сеть повторно.
    """
    a = from_currency.strip().upper()
    b = to_currency.strip().upper()
    if a == b:
        return 1.0

    table = rates if rates is not None else get_rates_table(base, timeout=timeout)
    base_u = base.strip().upper()

    if base_u not in table:
        table = get_rates_table(base_u, timeout=timeout)

    # В таблице всегда есть сама база с курсом 1.0
    def units_per_base(code: str) -> float:
        if code == base_u:
            return 1.0
        if code not in table:
            raise KeyError(f"Валюта {code!r} отсутствует в ответе для базы {base_u}.")
        return table[code]

    # X за 1 base: units_per_base(X). 1 A = (1 base / units_per_base(A)) ... удобнее через доли:
    # amount_B = amount_A * (units_per_base(B) / units_per_base(A))
    return units_per_base(b) / units_per_base(a)


def convert(
    amount: float,
    from_currency: str,
    to_currency: str,
    *,
    base: str = CCY_USD,
    rates: Optional[Dict[str, float]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> float:
    """
    Конвертирует ``amount`` из ``from_currency`` в ``to_currency``.

    Использует :func:`cross_rate`; при необходимости один раз загружает курсы.
    """
    return float(amount) * cross_rate(
        from_currency, to_currency, base=base, rates=rates, timeout=timeout
    )


def rub_thb_usd_matrix(
    *, base: str = CCY_USD, timeout: float = DEFAULT_TIMEOUT
) -> Dict[str, Dict[str, float]]:
    """
    Матрица курсов между RUB, THB и USD: ``matrix[from][to]`` — множитель для конвертации.

    Удобно для быстрого просмотра всех шести направлений без ручных вызовов.
    """
    rates = get_rates_table(base, timeout=timeout)
    out: Dict[str, Dict[str, float]] = {}
    for f in FOCUS_TRIO:
        out[f] = {}
        for t in FOCUS_TRIO:
            out[f][t] = cross_rate(f, t, base=base, rates=rates)
    return out


def _print_matrix(matrix: Dict[str, Dict[str, float]]) -> None:
    codes = list(FOCUS_TRIO)
    header = "        " + "".join(f"{t:>12}" for t in codes)
    print(header)
    for f in codes:
        row = f"{f:6}  " + "".join(f"{matrix[f][t]:12.6f}" for t in codes)
        print(row)
    print("\nСмысл: умножьте сумму в строке FROM на число в колонке TO.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Курсы RUB / THB / USD через open.er-api.com (ExchangeRate-API, без ключа)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_latest = sub.add_parser("latest", help="Сырой JSON ответа API для базы")
    p_latest.add_argument(
        "--base",
        default=CCY_USD,
        help="Базовая валюта (USD, RUB, THB, …)",
    )

    p_conv = sub.add_parser("convert", help="Конвертировать сумму")
    p_conv.add_argument("amount", type=float)
    p_conv.add_argument("from_ccy")
    p_conv.add_argument("to_ccy")
    p_conv.add_argument(
        "--base",
        default=CCY_USD,
        help="База для запроса к API (по умолчанию USD)",
    )

    p_rate = sub.add_parser("rate", help="Только курс пересчёта FROM -> TO (множитель)")
    p_rate.add_argument("from_ccy")
    p_rate.add_argument("to_ccy")
    p_rate.add_argument("--base", default=CCY_USD)

    p_matrix = sub.add_parser("matrix", help="Матрица RUB/THB/USD")
    p_matrix.add_argument("--base", default=CCY_USD, help="База запроса к API")
    return parser


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "latest":
            data = fetch_latest(args.base)
            print(json.dumps(data, ensure_ascii=False, indent=2))
        elif args.cmd == "convert":
            r = convert(args.amount, args.from_ccy, args.to_ccy, base=args.base)
            print(f"{args.amount} {args.from_ccy.upper()} = {r:.6f} {args.to_ccy.upper()}")
        elif args.cmd == "rate":
            r = cross_rate(args.from_ccy, args.to_ccy, base=args.base)
            print(f"1 {args.from_ccy.upper()} = {r:.8f} {args.to_ccy.upper()}")
        elif args.cmd == "matrix":
            m = rub_thb_usd_matrix(base=args.base)
            _print_matrix(m)
        else:
            return 2
        return 0
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
