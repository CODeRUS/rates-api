#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расчёт курса и суммы к получению RUB → THB по правилам ex24.pro (как в их фронтенде).

Актуальные курсы забираются с **главной** ``GET https://ex24.pro/``: в HTML встроен JSON
``"type":"rates","payload":{"rates":[...]}``. Нужная запись: ``from`` = RUB, ``to`` = THB,
при необходимости ``fromType`` = «по СБП». Поле **``rate``** — то, что показывают в интерфейсе;
**``realRate``** — база для наценки по сумме (как в их калькуляторе). При сбое разбора см.
``--real-rate`` и константу ``DEFAULT_REAL_RATE``.

Формула наценки (``markup`` в процентах к ``realRate``)::

    <= 1000 RUB      → +10%
    < 4000           → +7.5%
    < 7000           → +5.5%
    < 9000           → +4.5%
    < 14950          → +3.5%
    >= 14950         → 0%

Итоговый курс (RUB за 1 THB при отображении «сколько рублей за бат»)::

    rate = real_rate * (1 + markup / 100)

Сумма THB к выдаче::

    thb = amount_rub / rate
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Fallback, если не удалось разобрать главную (см. :func:`try_fetch_real_rate_rub_thb`).
DEFAULT_REAL_RATE = 2.7014

# Маркер массива курсов во встроенном JSON (Next.js с экранированными кавычками).
_PAYLOAD_RATES_MARKERS = (
    '\\"payload\\":{\\"rates\\":[',
    '"payload":{"rates":[',
)

# Минимальная сумма в RUB, с которой наценка к ``realRate`` = 0 % (см. :func:`markup_percent`).
RUB_MIN_FOR_ZERO_MARKUP = 14950.0

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"


def markup_percent(amount_rub: float) -> float:
    """Процент наценки к ``realRate`` в зависимости от суммы в RUB."""
    if amount_rub <= 1000:
        return 10.0
    if amount_rub < 4000:
        return 7.5
    if amount_rub < 7000:
        return 5.5
    if amount_rub < 9000:
        return 4.5
    if amount_rub < 14950:
        return 3.5
    return 0.0


def customer_rate_rub_per_thb(amount_rub: float, real_rate: float = DEFAULT_REAL_RATE) -> float:
    """
    Курс для клиента: сколько RUB за 1 THB (как на калькуляторе после наценки).

    Совпадает с ``getRubThbRate`` на ex24.pro.
    """
    m = markup_percent(amount_rub)
    return real_rate * (1.0 + m / 100.0)


def receive_thb(amount_rub: float, real_rate: float = DEFAULT_REAL_RATE) -> float:
    """Сколько THB выдадут за ``amount_rub`` RUB (до округления на стороне сайта)."""
    r = customer_rate_rub_per_thb(amount_rub, real_rate)
    return amount_rub / r


def _extract_rates_array_raw(html: str, start_bracket: int) -> Optional[str]:
    """Срез ``[...]`` верхнего массива ``rates``; ``start_bracket`` указывает на ``[``."""
    if start_bracket < 0 or start_bracket >= len(html) or html[start_bracket] != "[":
        return None
    depth = 1
    i = start_bracket + 1
    in_string = False
    n = len(html)
    while i < n:
        if not in_string:
            if html.startswith('\\"', i):
                in_string = True
                i += 2
                continue
            c = html[i]
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return html[start_bracket : i + 1]
            i += 1
            continue
        if html.startswith('\\"', i):
            in_string = False
            i += 2
            continue
        if html[i] == "\\" and i + 1 < n:
            i += 2
            continue
        i += 1
    return None


def parse_rates_array_from_ex24_html(html: str) -> Optional[List[Dict[str, Any]]]:
    """
    Достаёт ``payload.rates`` из HTML главной: экранирование ``\\\"`` как на ex24.pro.
    """
    start = -1
    for marker in _PAYLOAD_RATES_MARKERS:
        pos = html.find(marker)
        if pos != -1:
            start = pos + len(marker) - 1
            break
    if start < 0:
        return None
    raw = _extract_rates_array_raw(html, start)
    if not raw:
        return None
    try:
        return json.loads(raw.replace('\\"', '"'))
    except json.JSONDecodeError:
        return None


def pick_rub_thb_rate_row(
    rates: List[Dict[str, Any]],
    *,
    from_type: Optional[str] = "по СБП",
) -> Optional[Dict[str, Any]]:
    """Первая запись RUB→THB; при ``from_type`` — с совпадающим ``fromType``."""
    if from_type:
        for row in rates:
            if (
                row.get("from") == "RUB"
                and row.get("to") == "THB"
                and row.get("fromType") == from_type
            ):
                return row
    for row in rates:
        if row.get("from") == "RUB" and row.get("to") == "THB":
            return row
    return None


def _load_ex24_main_html(*, timeout: float = 25.0) -> Optional[str]:
    """Первый успешный HTML главной (ru root или /en)."""
    ctx = ssl.create_default_context()
    for url in ("https://ex24.pro/", "https://ex24.pro/en"):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            continue
    return None


def fetch_ex24_rub_thb_row(
    *,
    timeout: float = 25.0,
    from_type: Optional[str] = "по СБП",
) -> Optional[Dict[str, Any]]:
    """
    Загружает главную ex24.pro и возвращает объект направления RUB→THB из ``rates``
    (поля ``rate``, ``realRate``, …) или ``None``.
    """
    text = _load_ex24_main_html(timeout=timeout)
    if not text:
        return None
    rates = parse_rates_array_from_ex24_html(text)
    if not rates:
        return None
    return pick_rub_thb_rate_row(rates, from_type=from_type)


def _try_fetch_real_rate_regex(html: str) -> Optional[float]:
    """Запасной путь, если разбор ``rates[]`` не удался."""
    pattern_esc = re.compile(
        r'\\"from\\":\\"RUB\\",\\"to\\":\\"THB\\"[\s\S]{0,4000}?\\"realRate\\":([0-9]+(?:\.[0-9]+)?)',
    )
    pattern = re.compile(
        r'"from"\s*:\s*"RUB"\s*,\s*"to"\s*:\s*"THB"[\s\S]{0,4000}?"realRate"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        re.DOTALL,
    )
    pattern2 = re.compile(
        r'"realRate"\s*:\s*([0-9]+(?:\.[0-9]+)?)[\s\S]{0,2000}?"from"\s*:\s*"RUB"\s*,\s*"to"\s*:\s*"THB"',
        re.DOTALL,
    )
    for pat in (pattern_esc, pattern, pattern2):
        m = pat.search(html)
        if m:
            return float(m.group(1))
    return None


# Витрина «Курс обмена наличных»: в JSON встроено ``"RUB":{"buy":"0.3241","sell":"0.3919",...}``.
# Поле ``buy`` — THB за 1 RUB; сводка везде в **RUB за 1 THB** → ``1 / buy``.
_EX24_RUB_CASH_BUY_ESCAPED = re.compile(
    r'\\"RUB\\":\{\\"buy\\":\\"([0-9.]+)\\"'
)
_EX24_RUB_CASH_BUY_PLAIN = re.compile(
    r'"RUB"\s*:\s*\{\s*"buy"\s*:\s*"([0-9.]+)"'
)


def parse_ex24_cash_rub_buy_rub_per_thb(html: str) -> Optional[float]:
    """
    Курс **RUB за 1 THB** по наличным с главной: объект ``RUB`` с полем ``buy``
    (колонка «Отдаёте валюту» для рубля в блоке обмена наличных).
    """
    if not html:
        return None
    m = _EX24_RUB_CASH_BUY_ESCAPED.search(html)
    if not m:
        m = _EX24_RUB_CASH_BUY_PLAIN.search(html)
    if not m:
        return None
    thb_per_rub = float(m.group(1))
    if thb_per_rub <= 0:
        return None
    return 1.0 / thb_per_rub


def try_fetch_cash_rub_per_thb(*, timeout: float = 25.0) -> Optional[float]:
    """Главная ex24.pro → RUB за 1 THB из ``RUB.buy`` витрины наличных, или ``None``."""
    text = _load_ex24_main_html(timeout=timeout)
    if not text:
        return None
    return parse_ex24_cash_rub_buy_rub_per_thb(text)


def try_fetch_real_rate_rub_thb(
    *,
    timeout: float = 25.0,
    from_type: Optional[str] = "по СБП",
) -> Optional[float]:
    """
    Сначала ``payload.rates`` (приоритет ``realRate``), с той же страницы — запасной regex.

    Для расчёта наценки нужен ``realRate``; ``rate`` в той же записи — как в UI.
    """
    text = _load_ex24_main_html(timeout=timeout)
    if not text:
        return None
    rates = parse_rates_array_from_ex24_html(text)
    if rates:
        row = pick_rub_thb_rate_row(rates, from_type=from_type)
        if row is not None:
            rr = row.get("realRate")
            if rr is not None:
                return float(rr)
            rt = row.get("rate")
            if rt is not None:
                return float(rt)
    hit = _try_fetch_real_rate_regex(text)
    if hit is not None:
        return hit
    return None


@dataclass(frozen=True)
class Quote:
    amount_rub: float
    real_rate: float
    markup_pct: float
    customer_rate: float
    thb: float


def quote(amount_rub: float, real_rate: float = DEFAULT_REAL_RATE) -> Quote:
    m = markup_percent(amount_rub)
    cr = real_rate * (1.0 + m / 100.0)
    return Quote(
        amount_rub=amount_rub,
        real_rate=real_rate,
        markup_pct=m,
        customer_rate=cr,
        thb=amount_rub / cr,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="ex24.pro RUB→THB: курс с наценкой и THB к получению")
    p.add_argument("amount_rub", type=float, nargs="?", help="Сумма в RUB")
    p.add_argument(
        "--real-rate",
        type=float,
        default=None,
        help=f"Базовый realRate без наценки (по умолчанию {DEFAULT_REAL_RATE} или из сайта)",
    )
    p.add_argument(
        "--fetch-rate",
        action="store_true",
        help="Скачать главную и взять курс из payload.rates (RUB→THB)",
    )
    p.add_argument(
        "--from-type",
        default="по СБП",
        help="Значение fromType у записи RUB→THB (как на сайте; пусто — любая RUB→THB)",
    )
    p.add_argument("--table", action="store_true", help="Таблица наценок и примеров курса")
    p.add_argument(
        "--round-rate",
        type=int,
        default=None,
        metavar="N",
        help="Округлить отображаемый курс до N знаков (как на сайте, часто 3)",
    )
    return p


def cli_main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.table:
        rr = args.real_rate if args.real_rate is not None else DEFAULT_REAL_RATE
        print(f"realRate = {rr}\n")
        print("Диапазон RUB     markup   курс (RUB/THB)")
        samples = [500, 1000, 1001, 2500, 4000, 7000, 9000, 10000, 14949, 14950, 20000]
        for a in samples:
            q = quote(a, rr)
            dr = round(q.customer_rate, args.round_rate) if args.round_rate is not None else q.customer_rate
            print(f"  {a:>6}      {q.markup_pct:>4}%    {dr}")
        return 0

    if args.amount_rub is None:
        p.error("Укажите сумму в RUB или используйте --table")

    rr = args.real_rate
    if args.fetch_rate:
        ft = args.from_type.strip() or None
        got = False
        text = _load_ex24_main_html()
        row = None
        if text:
            rates = parse_rates_array_from_ex24_html(text)
            if rates:
                row = pick_rub_thb_rate_row(rates, from_type=ft)
        if row is not None:
            base = row.get("realRate")
            if base is None:
                base = row.get("rate")
            if base is not None:
                rr = float(base)
                got = True
                print(
                    f"(с сайта: rate={row.get('rate')}, realRate={row.get('realRate')}; "
                    f"база для наценки: {rr})",
                    flush=True,
                )
        if not got and text:
            fetched = _try_fetch_real_rate_regex(text)
            if fetched is not None:
                rr = fetched
                got = True
                print(f"(запасной разбор HTML: {fetched})", flush=True)
        if not got and rr is None:
            print(
                "Не удалось получить курс с сайта, используется значение по умолчанию.",
                flush=True,
            )
            rr = DEFAULT_REAL_RATE
    if rr is None:
        rr = DEFAULT_REAL_RATE

    q = quote(args.amount_rub, rr)
    cr = q.customer_rate
    if args.round_rate is not None:
        cr_disp = round(cr, args.round_rate)
    else:
        cr_disp = cr

    print(f"Сумма:        {q.amount_rub:g} RUB")
    print(f"realRate:     {q.real_rate}")
    print(f"Наценка:      {q.markup_pct:g}%")
    print(f"Курс клиента: {cr_disp} RUB за 1 THB (полное: {cr})")
    print(f"К получению:  {q.thb} THB (точное деление; сайт может округлить)")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
