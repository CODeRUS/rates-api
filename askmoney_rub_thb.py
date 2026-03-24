#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Калькулятор RUB → THB по правилам askmoney.pro.

Данные с главной страницы: GET https://askmoney.pro/
В HTML у скрытых полей есть ``data-prefill`` и в ``data-vals`` имя переменной (b2, f2, h2, b4).

Формула результата ``rub_bat_calc_out`` (переменная ввода суммы — b11 на сайте)::

    if RUB < 1000:        THB = 0
    elif RUB < h2 * f2:  THB = floor((RUB - 800) / b2 / 100) * 100
    else:                 THB = floor((RUB / b2 / b4) / 100) * 100

Эффективный курс (RUB за 1 THB), если THB > 0::

    rate = RUB / THB

Константа **800** и порог **1000** взяты из формулы в ``data-vals``; порог ``h2*f2``
совпадает с **14850** при h2=5500, f2=2.7.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

ASKMONEY_URL = "https://askmoney.pro/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36"

# Fallback, если парсинг не сработал (как в вашем примере).
DEFAULT_PARAMS = {"b2": 2.61, "f2": 2.7, "h2": 5500.0, "b4": 1.035}
RUB_MIN_PAYOUT = 1000
RUB_BRANCH_SUBTRACT = 800  # из формулы (b11-800)


@dataclass(frozen=True)
class AskMoneyParams:
    b2: float
    f2: float
    h2: float
    b4: float

    @property
    def threshold_rub(self) -> float:
        """Граница между «средней» и «крупной» веткой: h2 * f2 (часто 14850)."""
        return self.h2 * self.f2


def fetch_homepage_html(*, timeout: float = 30.0) -> str:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        ASKMONEY_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", errors="replace")


def _parse_prefill_for_variable(html: str, var: str) -> Optional[float]:
    """
    Ищет блок ``data-prefill="…"`` непосредственно перед ``variable":"var"`` в data-vals.
    """
    # Экранированные кавычки в HTML-атрибуте JSON
    needle = f'&quot;variable&quot;:&quot;{var}&quot;'
    for m in re.finditer(
        r'data-prefill="([^"]*)"',
        html,
    ):
        start = m.start()
        window = html[start : start + 800]
        if needle in window:
            raw = m.group(1).strip().replace(",", ".")
            try:
                return float(raw)
            except ValueError:
                return None
    return None


def parse_params_from_html(html: str) -> AskMoneyParams:
    """Извлекает b2, f2, h2, b4 со страницы."""
    vals: Dict[str, float] = {}
    for key in ("b2", "f2", "h2", "b4"):
        v = _parse_prefill_for_variable(html, key)
        if v is None:
            raise ValueError(f"Не найден data-prefill для переменной {key}")
        vals[key] = v
    return AskMoneyParams(b2=vals["b2"], f2=vals["f2"], h2=vals["h2"], b4=vals["b4"])


def rub_to_thb(rub: float, p: AskMoneyParams) -> int:
    """
    Сколько THB выдадут (целое, кратно 100 после floor — как на сайте).
    """
    if rub < RUB_MIN_PAYOUT:
        return 0
    thr = p.threshold_rub
    if rub < thr:
        return int(math.floor((rub - RUB_BRANCH_SUBTRACT) / p.b2 / 100.0) * 100)
    return int(math.floor((rub / p.b2 / p.b4) / 100.0) * 100)


def effective_rate_rub_per_thb(rub: float, thb: int) -> Optional[float]:
    """RUB за 1 THB; если THB == 0, возвращает None."""
    if thb <= 0:
        return None
    return rub / thb


def max_effective_rate_rub_per_thb(
    p: AskMoneyParams, *, integer_rub: bool = True
) -> Tuple[float, int, float]:
    """
    Максимум ``RUB / THB`` (наихудший для покупателя бата: больше рублей за бат).
    На ветке ``RUB < h2*f2``; дальше курс обычно ниже.

    На каждой «ступени» (постоянное THB) курс растёт с суммой, пик — у правой границы
    ступени. При ``integer_rub=True`` — целая сумма; иначе — точка чуть ниже следующего
    целого (сходится к ``(800+200*b2)/100``).
    """
    best_rub = 0.0
    best_thb = 0
    best_rate = 0.0
    thr = p.threshold_rub
    rub = float(RUB_MIN_PAYOUT)
    while rub < thr:
        thb = rub_to_thb(rub, p)
        if thb <= 0:
            rub += 1.0
            continue
        hi = rub
        while hi + 1 < thr and rub_to_thb(hi + 1, p) == thb:
            hi += 1
        if integer_rub:
            trial = hi
        else:
            trial = min(hi + 1.0 - 1e-9, thr - 1e-9)
        rt = effective_rate_rub_per_thb(trial, thb)
        if rt is not None and rt > best_rate:
            best_rate = rt
            best_rub = trial
            best_thb = thb
        rub = hi + 1.0
    return best_rub, best_thb, best_rate


def min_effective_rate_rub_per_thb(
    p: AskMoneyParams,
    *,
    rub_cap: float = 50_000_000.0,
) -> Tuple[float, int, float]:
    """
    Минимум ``RUB / THB`` (самый выгодный обмен: меньше рублей за бат).

    На каждой ступени при фиксированном THB курс ``RUB/THB`` растёт с суммой, значит
    минимум на ступени — у **левого** края. Дальше порога ``h2*f2`` курс колеблется
    около ``b2 * b4``; при росте суммы появляются точки с курсом, сколь угодно близким
    к ``b2*b4``. Возвращаем минимальный курс на ``RUB ∈ [1000, rub_cap]`` и **наименьшую**
    сумму RUB, при которой этот минимум достигается (при равенстве курса).
    """
    best_rate = float("inf")
    best_rub = float("inf")
    best_thb = 0
    thr = p.threshold_rub
    eps = 1e-9

    def consider(trial_rub: float, thb: int) -> None:
        nonlocal best_rate, best_rub, best_thb
        rt = effective_rate_rub_per_thb(trial_rub, thb)
        if rt is None:
            return
        if rt < best_rate - eps or (abs(rt - best_rate) <= eps and trial_rub < best_rub):
            best_rate = rt
            best_rub = trial_rub
            best_thb = thb

    def scan_segment(r_start: float, r_end: float) -> None:
        rub = float(r_start)
        while rub <= r_end:
            thb = rub_to_thb(rub, p)
            if thb <= 0:
                rub += 1.0
                continue
            lo = rub
            hi = rub
            while hi + 1.0 <= r_end and rub_to_thb(hi + 1.0, p) == thb:
                hi += 1.0
            consider(lo, thb)
            rub = hi + 1.0

    scan_segment(float(RUB_MIN_PAYOUT), min(thr - 1e-9, rub_cap))
    if thr <= rub_cap:
        scan_segment(thr, rub_cap)
    if best_rate == float("inf"):
        cap_s = f"{rub_cap:,.0f}".replace(",", " ")
        raise ValueError(
            f"Нет валидных сумм в диапазоне [1000, {cap_s}] RUB (увеличьте --rub-cap)"
        )
    return best_rub, best_thb, best_rate


def extract_rub_bat_formula(html: str) -> Optional[str]:
    """Сырой текст формулы из data-vals (для отладки)."""
    marker = '&quot;variable&quot;:&quot;rub_bat_calc_out&quot;'
    i = html.find(marker)
    if i < 0:
        return None
    chunk = html[i : i + 3000]
    m = re.search(r'&quot;formula&quot;:&quot;(.*?)&quot;,&quot;format&quot;', chunk, re.DOTALL)
    if not m:
        return None
    raw = m.group(1)
    return (
        raw.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("\\n", "\n")
    )


def load_params(fetch: bool, html_file: Optional[str]) -> AskMoneyParams:
    if html_file:
        with open(html_file, encoding="utf-8") as f:
            html = f.read()
        return parse_params_from_html(html)
    if fetch:
        html = fetch_homepage_html()
        return parse_params_from_html(html)
    return AskMoneyParams(**DEFAULT_PARAMS)  # type: ignore[arg-type]


def _main() -> int:
    p = argparse.ArgumentParser(description="askmoney.pro RUB→THB и курс")
    p.add_argument("rub", type=float, nargs="?", help="Сумма в RUB")
    p.add_argument(
        "--fetch",
        action="store_true",
        help="Скачать https://askmoney.pro/ и взять b2,f2,h2,b4",
    )
    p.add_argument(
        "--html-file",
        default=None,
        help="Путь к сохранённому HTML вместо сети",
    )
    p.add_argument("--show-formula", action="store_true", help="Показать формулу из HTML")
    p.add_argument("--json-params", action="store_true", help="Вывести параметры JSON")
    p.add_argument(
        "--max-rate",
        action="store_true",
        help="Наихудший курс: максимум RUB/THB (дороже бат; до порога h2*f2)",
    )
    p.add_argument(
        "--max-rate-float",
        action="store_true",
        help="Как --max-rate, но дробные рубли (верх ступени)",
    )
    p.add_argument(
        "--min-rate",
        action="store_true",
        help="Самый выгодный курс: минимум RUB/THB (дешевле бат), до --rub-cap RUB",
    )
    p.add_argument(
        "--rub-cap",
        type=float,
        default=50_000_000.0,
        metavar="RUB",
        help="Верхняя граница поиска для --min-rate (по умолчанию 50 млн)",
    )
    args = p.parse_args()

    try:
        params = load_params(args.fetch, args.html_file)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json_params:
        print(
            json.dumps(
                {
                    "b2": params.b2,
                    "f2": params.f2,
                    "h2": params.h2,
                    "b4": params.b4,
                    "threshold_rub": params.threshold_rub,
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    if args.show_formula and args.html_file:
        with open(args.html_file, encoding="utf-8") as f:
            h = f.read()
        print(extract_rub_bat_formula(h) or "formula not found")
    elif args.show_formula and args.fetch:
        h = fetch_homepage_html()
        print(extract_rub_bat_formula(h) or "formula not found")
    elif args.show_formula:
        print("Укажите --fetch или --html-file для --show-formula")

    if args.max_rate or args.max_rate_float:
        br, bt, brt = max_effective_rate_rub_per_thb(
            params, integer_rub=not args.max_rate_float
        )
        rub_s = f"{int(br)}" if args.max_rate else f"{br:.12f}".rstrip("0").rstrip(".")
        print(
            f"Наихудший курс (макс. RUB/THB): {brt:.6g} при RUB={rub_s} (THB={bt}). "
            f"Порог веток: {params.threshold_rub:g} RUB."
        )

    if args.min_rate:
        try:
            br, bt, brt = min_effective_rate_rub_per_thb(params, rub_cap=args.rub_cap)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 1
        asympt = params.b2 * params.b4
        cap_s = f"{args.rub_cap:,.0f}".replace(",", " ")
        print(
            f"Самый выгодный курс (мин. RUB/THB): {brt:.6g} при RUB={int(br)} (THB={bt}). "
            f"Предел при больших суммах ≈ b2×b4 = {asympt:.6g}. Поиск до {cap_s} RUB."
        )

    if args.rub is None:
        allowed = (
            args.json_params
            or args.show_formula
            or args.max_rate
            or args.max_rate_float
            or args.min_rate
        )
        if not allowed:
            p.error(
                "Укажите сумму RUB или --json-params / --show-formula / --max-rate / --min-rate"
            )
        return 0

    thb = rub_to_thb(args.rub, params)
    rate = effective_rate_rub_per_thb(args.rub, thb)
    print(f"Параметры: b2={params.b2} f2={params.f2} h2={params.h2} b4={params.b4} | порог RUB={params.threshold_rub:g}")
    print(f"Сумма:     {args.rub:g} RUB")
    print(f"THB:       {thb}")
    if rate is not None:
        print(f"Курс:      {rate:g} RUB за 1 THB (RUB/THB)")
    else:
        print("Курс:      — (THB = 0 при сумме < 1000 RUB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
