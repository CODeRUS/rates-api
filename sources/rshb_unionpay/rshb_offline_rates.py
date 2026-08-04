#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Архив курсов РСХБ для карточных операций вне сети банка (HTML, не JSON API).

URL (как на скриншоте): https://old.rshb.ru/natural/cards/rates/rates_offline/

Страница содержит блоки ``<strong>DD.MM.YYYY</strong>`` и таблицы с парами
EUR/USD, USD/CNY, CNY/RUR, USD/RUR, EUR/RUR и колонками ПОКУПКА / ПРОДАЖА.

Для модели UnionPay с **рублёвой картой** (оплата/снятие через CNY) берётся
колонка **ПРОДАЖА** по паре **CNY/RUR**: курс **продажи CNY за RUB** (RUB за 1 CNY).
"""

from __future__ import annotations

import logging
import re
import shutil
import ssl
import subprocess
import urllib.request
from dataclasses import dataclass

from rates_http import urlopen_retriable
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

RSHB_OFFLINE_URL = "https://old.rshb.ru/natural/cards/rates/rates_offline/"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_ROW_RE = re.compile(
    r"<tr>\s*<td>([^<]+)</td>\s*<td>([0-9.]+)</td>\s*<td>([0-9.]+)</td>\s*</tr>",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PairQuote:
    pair: str
    buy: Decimal
    sell: Decimal


def _fetch_offline_page_curl(
    *,
    timeout: float,
    attempts: int = 5,
) -> str:
    """
    Скачивание через curl.

    Сеть до old.rshb.ru часто обрывает длинные ответы (~20 KB из ~130 KB).
    Gzip (~26 KB) обычно проходит целиком; при сбое — короткие повторы.
    ``urllib`` к этому хосту зависает на теле ответа — не используем как основной путь.
    """
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("curl не найден в PATH")
    # Fail-fast: при «залипании» после ~20 KB не ждать полный timeout.
    per_try = max(8.0, min(float(timeout), 15.0))
    n_try = max(1, int(attempts))
    last_err: Optional[BaseException] = None
    for i in range(n_try):
        cmd = [
            curl,
            "-fsS",
            "-k",  # Russian Trusted CA часто нет в trust store
            "--compressed",  # gzip: меньше байт на проводе — реже обрыв
            "-m",
            str(int(round(per_try))),
            "-A",
            USER_AGENT,
            "-H",
            "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "-H",
            "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8",
            RSHB_OFFLINE_URL,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=per_try + 5.0,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            last_err = TimeoutError(f"РСХБ offline curl: timeout {per_try}s")
            logger.debug("РСХБ offline curl try %s/%s timeout", i + 1, n_try)
            continue
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[:300]
            last_err = RuntimeError(f"РСХБ offline curl exit {proc.returncode}: {err}")
            logger.debug("РСХБ offline curl try %s/%s: %s", i + 1, n_try, err.strip())
            continue
        text = (proc.stdout or b"").decode("utf-8", errors="replace")
        if "CNY/RUR" not in text and "CNY/RUB" not in text:
            last_err = RuntimeError(
                f"РСХБ offline curl: ответ без CNY/RUR ({len(text)} bytes)"
            )
            continue
        return text
    assert last_err is not None
    raise last_err


def _fetch_offline_page_urllib(
    *, timeout: float, max_attempts: Optional[int] = None
) -> str:
    # old.rshb.ru: сертификат Russian Trusted CA — часто нет в системном trust store.
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        RSHB_OFFLINE_URL,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
            "Accept-Encoding": "identity",
        },
    )
    kw: Dict[str, Any] = {"timeout": timeout, "context": ctx}
    if max_attempts is not None:
        kw["max_attempts_override"] = max_attempts
    with urlopen_retriable(req, **kw) as r:
        return r.read().decode("utf-8", errors="replace")


def fetch_offline_page(*, timeout: float = 60.0, max_attempts: Optional[int] = None) -> str:
    """
    HTML архива rates_offline.

    Основной транспорт — curl+gzip с повторами. urllib только если curl нет в PATH
    (на практике к old.rshb.ru urllib часто зависает mid-body).
    """
    attempts = max(3, int(max_attempts) if max_attempts is not None else 5)
    if shutil.which("curl"):
        return _fetch_offline_page_curl(timeout=timeout, attempts=attempts)
    return _fetch_offline_page_urllib(timeout=timeout, max_attempts=1)


def parse_offline_html(
    html: str,
    *,
    duplicate_date_policy: str = "last",
) -> Dict[date, List[PairQuote]]:
    """
    Разбирает страницу на словарь ``date -> список котировок``.

    Берёт только первую таблицу сразу после каждой метки даты (как на сайте).

    ``duplicate_date_policy``: на ``rates_offline`` у каждой даты обычно один блок;
    на ``rates_online`` подряд идёт несколько снимков с **одной** датой, причём
    первый блок часто содержит только часть кросс-курсов (без CNY/RUR). Такие
    блоки **объединяются**: для ``"first"`` по каждой паре берётся первое появление
    на странице, для ``"last"`` — последнее.
    """
    # Режем по <strong>DD.MM.YYYY</strong>
    parts = re.split(
        r'<strong>\s*(\d{2})\.(\d{2})\.(\d{4})\s*</strong>',
        html,
        flags=re.IGNORECASE,
    )
    out: Dict[date, List[PairQuote]] = {}
    # parts[0] — преамбула, далее тройки (d,m,y, fragment)
    i = 1
    while i + 3 <= len(parts):
        d, m, y, frag = parts[i], parts[i + 1], parts[i + 2], parts[i + 3]
        dt = date(int(y), int(m), int(d))
        # до следующей даты или разумного предела — иначе подхватим чужую таблицу
        stop = re.search(
            r"<strong>\s*\d{2}\.\d{2}\.\d{4}\s*</strong>", frag, flags=re.IGNORECASE
        )
        chunk = frag[: stop.start()] if stop else frag[:20000]
        quotes: List[PairQuote] = []
        for mrow in _ROW_RE.finditer(chunk):
            pair = mrow.group(1).strip()
            buy = Decimal(mrow.group(2))
            sell = Decimal(mrow.group(3))
            quotes.append(PairQuote(pair, buy, sell))
        if quotes:
            if dt in out:
                _merge_duplicate_date_quotes(
                    out, dt, quotes, duplicate_date_policy=duplicate_date_policy
                )
            else:
                out[dt] = quotes
        i += 4
    return out


def _norm_pair_key(pair: str) -> str:
    return pair.replace(" ", "").upper()


def _merge_duplicate_date_quotes(
    out: Dict[date, List[PairQuote]],
    dt: date,
    quotes: List[PairQuote],
    *,
    duplicate_date_policy: str,
) -> None:
    """
    На ``rates_online`` подряд идут несколько блоков с одной календарной датой;
    первый блок часто содержит только часть кросс-курсов (без CNY/RUR).
    Объединяем строки так, чтобы для каждой пары курс брался с ожидаемого снимка.
    """
    prev = out[dt]
    if duplicate_date_policy == "first":
        by_pair = {_norm_pair_key(q.pair): q for q in prev}
        order = [_norm_pair_key(q.pair) for q in prev]
        for q in quotes:
            k = _norm_pair_key(q.pair)
            if k not in by_pair:
                by_pair[k] = q
                order.append(k)
        out[dt] = [by_pair[k] for k in order]
        return
    by_pair = {_norm_pair_key(q.pair): q for q in prev}
    for q in quotes:
        by_pair[_norm_pair_key(q.pair)] = q
    out[dt] = list(by_pair.values())


def get_table_for_date(
    html: Optional[str] = None,
    *,
    on: Optional[date] = None,
    timeout: float = 60.0,
) -> List[PairQuote]:
    """Возвращает таблицу на конкретную дату (по умолчанию самую свежую на странице)."""
    raw = html if html is not None else fetch_offline_page(timeout=timeout)
    tables = parse_offline_html(raw)
    if not tables:
        raise ValueError("Не удалось распарсить таблицы курсов")
    if on is not None:
        if on not in tables:
            raise KeyError(f"Нет данных на {on.isoformat()} (есть: {sorted(tables.keys(), reverse=True)[:5]}…)")
        return tables[on]
    latest = max(tables.keys())
    return tables[latest]


def cny_rur_sell(*, on: Optional[date] = None, html: Optional[str] = None) -> Decimal:
    """CNY/RUR, колонка ПРОДАЖА (банк продаёт клиенту CNY за RUB)."""
    rows = get_table_for_date(html, on=on)
    for q in rows:
        if q.pair.replace(" ", "").upper() in ("CNY/RUR", "CNY/RUB"):
            return q.sell
    raise KeyError("Пара CNY/RUR не найдена")


def cny_rur_buy(*, on: Optional[date] = None, html: Optional[str] = None) -> Decimal:
    """CNY/RUR, колонка ПОКУПКА (банк покупает CNY)."""
    rows = get_table_for_date(html, on=on)
    for q in rows:
        if q.pair.replace(" ", "").upper() in ("CNY/RUR", "CNY/RUB"):
            return q.buy
    raise KeyError("Пара CNY/RUR не найдена")


def cli_main(argv=None) -> int:
    if argv:
        import sys

        print("rshb_offline_rates: без аргументов; выгрузка последней таблицы.", file=sys.stderr)
        return 2
    raw = fetch_offline_page()
    tabs = parse_offline_html(raw)
    print("Даты в архиве (пример):", sorted(tabs.keys(), reverse=True)[:5])
    t = get_table_for_date(raw)
    for q in t:
        print(q)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
