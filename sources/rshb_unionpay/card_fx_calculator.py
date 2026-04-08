#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Калькулятор сценариев «THB ↔ RUB / CNY» в духе примеров с UnionPay + MOEX + РСХБ.

Это **модель** по наблюдаемым формулам из ваших расчётов; курсы АТБ и др. задаются
параметрами, если не подставлены из внешних источников.

## Откуда данные

* **UnionPay** — дневной файл ``YYYYMMDD.json`` (см. :mod:`unionpay_rates`).
* **MOEX** — CNYRUB_TOM (см. :mod:`moex_fx`).
* **РСХБ offline** — HTML ``rates_offline``, CNY/RUR **продажа** для рублёвой карты
  (курс продажи CNY за RUB; см. :mod:`rshb_offline_rates`).
* **РСХБ online** — HTML ``rates_online``, CNY/RUR **продажа** для операций в сети банка
  («приложение», юаневая карта; см. :mod:`rshb_online_rates`).

## Ключевые формулы (проверены на ваших числах)

1. **1 THB в CNY** (UnionPay): ``cny_per_thb = rate(THB→CNY)`` из JSON.

2. **1 THB в RUB через юань** (юаневая карта, без комиссии банка-эмитента):
   ``rub_per_thb = cny_per_thb * cny_rub_effective``,
   где ``cny_rub_effective`` — выбранный канал (MOEX, **ПРОДАЖА CNY/RUR**
   с ``rates_online`` (продажа CNY) для «приложения», **продажа CNY/RUR** с
   ``rates_offline`` для рублёвой карты и т.д.).

3. **Оплата** на сумму ``thb``: ``rub = thb * cny_per_thb * cny_rub_effective``.

4. **Снятие** (нетто ``thb_net``, комиссия банкомата ``atm_fee_thb``):
   база в батах к конвертации UnionPay: ``thb_gross = thb_net + atm_fee_thb``.
   База в CNY: ``base_cny = thb_gross * cny_per_thb``.
   Для юаневой карты РСХБ в примере сверху начисляется **3%** от этой базы в CNY:
   ``cny_debit = base_cny * 1.03`` (3% = 190.96 при base_cny ≈ 6365.27).
   Итог в RUB: ``rub = cny_debit * cny_rub_effective``.
   **Курс «за 1 THB»** в ваших таблицах для снятия — чаще ``rub / thb_net`` (делитель —
   нетто 30 000, а не gross).

5. **Рублёвая карта, снятие**: ``base_rub = base_cny * cny_rub_offline_sell`` (без +3% к CNY),
   плюс **комиссия банка в RUB** — в модели по умолчанию **1%** от ``base_rub``
   (``extra_rub_pct_of_base=0.01``); опционально добавляется фикс ``extra_rub_fee``.

6. **Порог «лучшего курса»** (процент вместо фикса):
   * юаневая карта: ``(T + atm_fee) * cny_per_thb * pct = flat_cny``
     → ``T_min = flat_cny / (pct * cny_per_thb) - atm_fee`` (у вас 17 CNY, 3%, 250 THB → ≈2443).
   * рублевая: ``(T + atm_fee) * cny_per_thb * cny_rub_sell * pct_rub = flat_rub``
     → ``T_min = flat_rub / (pct_rub * cny_per_thb * cny_rub_sell) - atm_fee`` (199 RUB, 1% → ≈6893).
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.error
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from . import moex_fx
from . import rshb_offline_rates
from . import rshb_online_rates
from . import unionpay_rates

# Корень репозитория: sources/rshb_unionpay/this_file -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]
# Последний успешный снимок UnionPay + MOEX + РСХБ (для расчётов при таймауте сети).
LIVE_INPUTS_CACHE_FILE = _REPO_ROOT / ".card_fx_live_inputs_cache.json"


def _is_timeout_error(exc: BaseException) -> bool:
    """True, если исключение связано с таймаутом HTTP/сокета."""
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, socket.timeout):
        return True
    if isinstance(exc, OSError):
        en = getattr(exc, "errno", None)
        if en in (110, 10060):  # ETIMEDOUT / WinError WSAETIMEDOUT
            return True
        err = str(exc).lower()
        if "timed out" in err or "time out" in err:
            return True
    if isinstance(exc, urllib.error.URLError):
        r = exc.reason
        if isinstance(r, BaseException):
            return _is_timeout_error(r)
        if isinstance(r, str) and "timed out" in r.lower():
            return True
    return False


def _is_missing_online_cny_error(exc: BaseException) -> bool:
    """True, если rates_online не содержит CNY/RUR на доступных датах."""
    msg = str(exc)
    if "Пара CNY/RUR не найдена на rates_online" in msg:
        return True
    return False


def _save_live_inputs_cache(
    cpt: float,
    moex: float,
    sell: Decimal,
    rshb_on: date,
    online_sell: Decimal,
    rshb_online_on: date,
    unionpay_payload: Dict[str, Any],
) -> None:
    payload = {
        "saved_unix": time.time(),
        "cny_per_thb": cpt,
        "moex_cny_rub": moex,
        "rshb_cny_rur_sell": str(sell),
        "rshb_table_date": rshb_on.isoformat(),
        "rshb_online_cny_rur_sell": str(online_sell),
        "rshb_online_table_date": rshb_online_on.isoformat(),
        "unionpay_payload": unionpay_payload,
    }
    try:
        LIVE_INPUTS_CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _load_live_inputs_cache() -> Optional[
    Tuple[float, float, Decimal, date, Decimal, date, Dict[str, Any]]
]:
    if not LIVE_INPUTS_CACHE_FILE.is_file():
        return None
    try:
        raw = json.loads(LIVE_INPUTS_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    try:
        cpt = float(raw["cny_per_thb"])
        moex = float(raw["moex_cny_rub"])
        sell = Decimal(
            str(
                raw.get("rshb_cny_rur_sell")
                or raw.get("rshb_cny_rur_buy")  # кеш после эксперимента с ПОКУПКА
            )
        )
        rshb_on = date.fromisoformat(str(raw["rshb_table_date"]))
        online_sell = Decimal(str(raw["rshb_online_cny_rur_sell"]))
        rshb_online_on = date.fromisoformat(str(raw["rshb_online_table_date"]))
        up = raw.get("unionpay_payload")
        if not isinstance(up, dict):
            return None
        return cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up
    except (KeyError, TypeError, ValueError):
        return None


def _fetch_live_inputs_network(
    on: Optional[date],
    moex_override: Optional[float],
) -> Tuple[float, float, Decimal, date, Decimal, date, Dict[str, Any]]:
    """Сетевой сбор без кеша при ошибке."""
    up = unionpay_rates.fetch_daily_file(on)
    cpt = unionpay_rates.cny_per_thb(cache=up)
    moex = float(moex_override) if moex_override is not None else moex_fx.cny_rub_tom()
    raw = rshb_offline_rates.fetch_offline_page()
    tables = rshb_offline_rates.parse_offline_html(raw)
    if not tables:
        raise RuntimeError("РСХБ rates_offline: нет таблиц курсов на странице")
    rshb_on = on
    if rshb_on is None or rshb_on not in tables:
        rshb_on = max(tables.keys())
    sell = rshb_offline_rates.cny_rur_sell(on=rshb_on, html=raw)

    raw_on = rshb_online_rates.fetch_online_page()
    tables_on = rshb_online_rates.parse_online_html(raw_on)
    if not tables_on:
        raise RuntimeError("РСХБ rates_online: нет таблиц курсов на странице")
    rshb_online_on = on
    if rshb_online_on is None:
        # Автовыбор: cny_rur_sell сам проверяет текущую страницу и archive range.
        online_sell = rshb_online_rates.cny_rur_sell(on=None, html=raw_on)
        rshb_online_on = max(tables_on.keys())
    else:
        # Явная дата: если её нет на текущей странице, cny_rur_sell проверит archive range.
        online_sell = rshb_online_rates.cny_rur_sell(on=rshb_online_on, html=raw_on)

    return cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up


@dataclass
class ChannelRates:
    """Эффективные RUB за 1 CNY под разные каналы (как в вашем списке)."""

    name: str
    cny_rub: float


def default_channels(
    moex_cny_rub: float,
    rshb_cny_rur_sell: float,
    rshb_app_cny_rub: float = 12.08,
    atb_cny: float = 12.21,
    atb_rub_conv: float = 12.247,
) -> list[ChannelRates]:
    """
    Набор каналов по умолчанию (цифры как в вашем примере от 23.03.2026).

    ``moex_cny_rub`` — референс с биржи (MOEX CNYRUB_TOM).
    ``rshb_app_cny_rub`` — CNY/RUR **продажа** с ``rates_online`` (операции в сети банка).
    """
    return [
        ChannelRates("MOEX (CNY/RUB)", moex_cny_rub),
        ChannelRates("РСХБ банк (приложение)", rshb_app_cny_rub),
        ChannelRates("АТБ CNY", atb_cny),
        ChannelRates("АТБ RUB конвертация", atb_rub_conv),
        ChannelRates("РСХБ RUB карта (CNY/RUR продажа)", rshb_cny_rur_sell),
    ]


def rub_per_thb(cny_per_thb: float, cny_rub: float) -> float:
    return cny_per_thb * cny_rub


def payment_rub(thb: float, cny_per_thb: float, cny_rub: float) -> float:
    return thb * cny_per_thb * cny_rub


def atm_cny_debit_rshb(
    thb_net: float,
    atm_fee_thb: float,
    cny_per_thb: float,
    issuer_fee_on_cny_base: float = 0.03,
) -> tuple[float, float, float]:
    """
    Юаневая карта: возвращает (base_cny, issuer_fee_cny, total_cny).
    ``issuer_fee_on_cny_base`` — доля сверх базы (0.03 = 3%).
    """
    gross_thb = thb_net + atm_fee_thb
    base = gross_thb * cny_per_thb
    fee = base * issuer_fee_on_cny_base
    return base, fee, base + fee


def atm_rub_from_cny_path(
    thb_net: float,
    atm_fee_thb: float,
    cny_per_thb: float,
    cny_rub: float,
    issuer_fee_on_cny_base: float = 0.03,
    extra_rub_fee: float = 0.0,
    extra_rub_pct_of_base: float = 0.0,
) -> tuple[float, float]:
    """
    Итог RUB и отображаемый rub_per_thb (деление на thb_net).

    ``extra_rub_pct_of_base`` — доля сверх рублёвой базы ``cny_tot * cny_rub``
    (для рублёвой карты при снятии обычно **0.01** = 1%).
    """
    _, _, cny_tot = atm_cny_debit_rshb(
        thb_net, atm_fee_thb, cny_per_thb, issuer_fee_on_cny_base
    )
    base_rub = cny_tot * cny_rub
    rub = base_rub * (1.0 + extra_rub_pct_of_base) + extra_rub_fee
    if thb_net <= 0:
        return rub, float("inf")
    return rub, rub / thb_net


def atm_rub_total_for_net(
    thb_net: float,
    *,
    atm_fee_thb: float,
    cny_per_thb: float,
    cny_rub: float,
    rub_card: bool,
    issuer_cny_atm_pct: float = 0.03,
    rub_card_atm_pct: float = 0.01,
) -> float:
    """Сколько RUB спишут при снятии ``thb_net`` THB (нетто) с комиссией ATM."""
    rub, _ = (
        atm_rub_from_cny_path(
            thb_net,
            atm_fee_thb,
            cny_per_thb,
            cny_rub,
            issuer_fee_on_cny_base=0.0,
            extra_rub_pct_of_base=rub_card_atm_pct,
        )
        if rub_card
        else atm_rub_from_cny_path(
            thb_net,
            atm_fee_thb,
            cny_per_thb,
            cny_rub,
            issuer_fee_on_cny_base=issuer_cny_atm_pct,
            extra_rub_fee=0.0,
        )
    )
    return rub


def max_thb_net_for_atm_rub_budget(
    budget_rub: float,
    *,
    cny_per_thb: float,
    atm_fee_thb: float,
    cny_rub: float,
    rub_card: bool,
    issuer_cny_atm_pct: float = 0.03,
    rub_card_atm_pct: float = 0.01,
) -> float:
    """
    Максимальное нетто THB, если списание RUB при одном снятии не превышает ``budget_rub``.

    Монотонность ``atm_rub_total_for_net`` по ``thb_net``; ищем верхнюю границу бисекцией.
    """
    if budget_rub <= 0 or cny_per_thb <= 0 or cny_rub <= 0:
        return 0.0
    cost0 = atm_rub_total_for_net(
        0.0,
        atm_fee_thb=atm_fee_thb,
        cny_per_thb=cny_per_thb,
        cny_rub=cny_rub,
        rub_card=rub_card,
        issuer_cny_atm_pct=issuer_cny_atm_pct,
        rub_card_atm_pct=rub_card_atm_pct,
    )
    if cost0 > budget_rub:
        return 0.0
    lo = 0.0
    hi = 1.0
    while (
        atm_rub_total_for_net(
            hi,
            atm_fee_thb=atm_fee_thb,
            cny_per_thb=cny_per_thb,
            cny_rub=cny_rub,
            rub_card=rub_card,
            issuer_cny_atm_pct=issuer_cny_atm_pct,
            rub_card_atm_pct=rub_card_atm_pct,
        )
        <= budget_rub
    ):
        hi *= 2.0
        if hi > 1e12:
            break
    for _ in range(96):
        mid = (lo + hi) / 2.0
        if (
            atm_rub_total_for_net(
                mid,
                atm_fee_thb=atm_fee_thb,
                cny_per_thb=cny_per_thb,
                cny_rub=cny_rub,
                rub_card=rub_card,
                issuer_cny_atm_pct=issuer_cny_atm_pct,
                rub_card_atm_pct=rub_card_atm_pct,
            )
            <= budget_rub
        ):
            lo = mid
        else:
            hi = mid
    return lo


def min_thb_for_cny_percent_fee(
    flat_fee_cny: float,
    pct: float,
    cny_per_thb: float,
    atm_fee_thb: float,
) -> float:
    """Минимальное нетто THB, при котором ``pct`` от base_cny даёт ровно ``flat_fee_cny``."""
    return flat_fee_cny / (pct * cny_per_thb) - atm_fee_thb


def min_thb_for_rub_percent_fee(
    flat_fee_rub: float,
    pct_rub: float,
    cny_per_thb: float,
    cny_rub_sell: float,
    atm_fee_thb: float,
) -> float:
    """Порог нетто THB: при комиссии ``pct_rub`` от рублёвой базы снятия она равна ``flat_fee_rub``."""
    return flat_fee_rub / (pct_rub * cny_per_thb * cny_rub_sell) - atm_fee_thb


def fetch_live_inputs(
    on: Optional[date] = None,
    moex_override: Optional[float] = None,
    *,
    use_cache_on_timeout: bool = True,
    readonly: bool = False,
) -> tuple[float, float, Decimal, date, Decimal, date, bool, Dict[str, Any]]:
    """
    UnionPay THB→CNY, MOEX CNY/RUB, РСХБ CNY/RUR (offline + online), даты таблиц.

    Возвращает
    ``(cny_per_thb, moex, rshb_offline_sell, rshb_offline_date,
      rshb_online_sell, rshb_online_date, used_stale_cache, unionpay_payload)``.

    ``rshb_offline_*`` — ``rates_offline``, рублёвая карта: CNY/RUR **продажа**.
    ``rshb_online_*`` — ``rates_online``, юаневая карта (приложение): CNY/RUR **продажа**.

    При **таймауте** сети и ``use_cache_on_timeout=True`` подставляются значения из
    ``.card_fx_live_inputs_cache.json`` (последний успешный запуск), флаг
    ``used_stale_cache`` = True.

    ``readonly=True`` — только файл ``.card_fx_live_inputs_cache.json``, без HTTP.

    ``unionpay_payload`` — тот же объект, что у :func:`unionpay_rates.fetch_daily_file`,
    для передачи в ``cache=`` в отчётах.
    """
    if readonly:
        cached = _load_live_inputs_cache()
        if cached is None:
            raise RuntimeError(
                "readonly: нет файла .card_fx_live_inputs_cache.json "
                "(нужен хотя бы один успешный онлайн-сбор курсов)."
            )
        cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up = cached
        return cpt, moex, sell, rshb_on, online_sell, rshb_online_on, True, up
    try:
        cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up = (
            _fetch_live_inputs_network(on, moex_override)
        )
        _save_live_inputs_cache(
            cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up
        )
        return cpt, moex, sell, rshb_on, online_sell, rshb_online_on, False, up
    except BaseException as e:
        cached = None
        if use_cache_on_timeout and (
            _is_timeout_error(e) or _is_missing_online_cny_error(e)
        ):
            cached = _load_live_inputs_cache()
        if cached is not None:
            cpt, moex, sell, rshb_on, online_sell, rshb_online_on, up = cached
            return (
                cpt,
                moex,
                sell,
                rshb_on,
                online_sell,
                rshb_online_on,
                True,
                up,
            )
        raise


def _msk_now_str() -> str:
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo("Europe/Moscow")).strftime(
                "%d.%m.%Y, %H:%M (MSK)"
            )
        except Exception:
            pass
    return datetime.now().strftime("%d.%m.%Y, %H:%M (локальное время)")


@dataclass
class Example1Channel:
    """Один ряд как в примере 1): оплата и снятие."""

    label_payment: str
    label_atm: str
    cny_rub: float
    rub_card: bool


def _example1_channels(
    moex_cny_rub: float,
    rshb_cny_rur_sell: float,
    rshb_table_date: date,
    rshb_app_cny_rub: float = 12.08,
    atb_cny: float = 12.21,
    atb_rub_conv: float = 12.247,
) -> List[Example1Channel]:
    d = rshb_table_date.isoformat()
    return [
        Example1Channel(
            "MOEX (CNY/RUB)",
            "MOEX (CNY/RUB)",
            moex_cny_rub,
            False,
        ),
        Example1Channel("АТБ CNY", "АТБ CNY", atb_cny, False),
        Example1Channel("АТБ RUB", "АТБ RUB", atb_rub_conv, False),
        Example1Channel(
            "РСХБ CNY (РСХБ-приложение)",
            "РСХБ CNY (РСХБ-приложение)",
            rshb_app_cny_rub,
            False,
        ),
        Example1Channel(
            f"РСХБ RUB ({d})",
            f"РСХБ RUB ({d})",
            rshb_cny_rur_sell,
            True,
        ),
    ]


def pct_vs_moex_cny_rub(cny_leg: float, moex: float) -> float:
    """Процент отклонения курса CNY/RUB от MOEX (как в сноске * для оплаты)."""
    return (cny_leg / moex - 1.0) * 100.0


def _rshb_report_channels(
    *,
    moex_cny_rub: float,
    rshb_cny_rur_sell: float,
    rshb_table_date: date,
    rshb_app_cny_rub: float,
) -> List[Example1Channel]:
    d = rshb_table_date.isoformat()
    return [
        Example1Channel(
            "РСХБ CNY (РСХБ-брокер)",
            "РСХБ CNY (РСХБ-брокер)",
            moex_cny_rub,
            False,
        ),
        Example1Channel(
            "РСХБ CNY (РСХБ-приложение)",
            "РСХБ CNY (РСХБ-приложение)",
            rshb_app_cny_rub,
            False,
        ),
        Example1Channel(
            f"РСХБ RUB ({d})",
            f"РСХБ RUB ({d})",
            rshb_cny_rur_sell,
            True,
        ),
    ]


def build_rshb_text(
    *,
    thb_nets: Sequence[float] = (30_000.0,),
    atm_fee_thb: float = 250.0,
    on: Optional[date] = None,
    moex_override: Optional[float] = None,
    rub_card_atm_pct: float = 0.01,
    issuer_cny_atm_pct: float = 0.03,
    readonly: bool = False,
) -> str:
    """
    Единый текстовый отчёт THB/RUB для команды `rshb` (CLI и bot).

    ``thb_nets`` — одна или несколько сумм нетто-снятия THB; для каждой повторяется
    блок «🏧 СНЯТИЕ …» при общих курсах и ``atm_fee_thb``.
    """
    amounts = list(thb_nets)
    if not amounts:
        amounts = [30_000.0]

    cpt, moex, rshb_sell_dec, rshb_date, online_sell_dec, _, _stale, _ = fetch_live_inputs(
        on, moex_override, readonly=readonly
    )
    rshb_sell = float(rshb_sell_dec)
    channels = _rshb_report_channels(
        moex_cny_rub=moex,
        rshb_cny_rur_sell=rshb_sell,
        rshb_table_date=rshb_date,
        rshb_app_cny_rub=float(online_sell_dec),
    )

    atm_fee_display = f"{atm_fee_thb:,.2f}".replace(",", " ")

    lines: List[str] = ["Курс THB/RUB:", "", "💳 ОПЛАТА картами UnionPay"]
    for ch in channels:
        pay_rpt = rub_per_thb(cpt, ch.cny_rub)
        p = pct_vs_moex_cny_rub(ch.cny_rub, moex)
        lines.append(f"• {pay_rpt:.3f} RUB за 1 THB | {ch.label_payment} ({p:.3f}%)*")

    moex_rub_per_thb = rub_per_thb(cpt, moex)
    for thb_net in amounts:
        thb_display = f"{thb_net:,.2f}".replace(",", " ")
        lines.extend(
            [
                "",
                f"🏧 СНЯТИЕ {thb_display} THB в банкомате",
                f"(с учётом комиссии банкомата {atm_fee_display} THB)",
            ]
        )
        for ch in channels:
            if ch.rub_card:
                _, atm_rpt = atm_rub_from_cny_path(
                    thb_net,
                    atm_fee_thb,
                    cpt,
                    ch.cny_rub,
                    issuer_fee_on_cny_base=0.0,
                    extra_rub_pct_of_base=rub_card_atm_pct,
                )
            else:
                _, atm_rpt = atm_rub_from_cny_path(
                    thb_net,
                    atm_fee_thb,
                    cpt,
                    ch.cny_rub,
                    issuer_fee_on_cny_base=issuer_cny_atm_pct,
                    extra_rub_fee=0.0,
                )
            atm_pct = (atm_rpt / moex_rub_per_thb - 1.0) * 100.0
            lines.append(f"• {atm_rpt:.3f} RUB за 1 THB | {ch.label_atm} ({atm_pct:.1f}%)*")

    lines.extend(
        [
            "",
            "*разница от биржевого курса MOEX CNY/RUB",
            "",
            _msk_now_str(),
        ]
    )
    return "\n".join(lines) + "\n"


def report_example1(
    thb_net: float = 30_000.0,
    atm_fee_thb: float = 250.0,
    on: Optional[date] = None,
    moex_override: Optional[float] = None,
    rub_card_atm_pct: float = 0.01,
    issuer_cny_atm_pct: float = 0.03,
) -> None:
    """
    Вывод в стиле вашего пункта 1): оплата и снятие.

    Сноска * для **оплаты**: отклонение CNY/RUB канала от MOEX CNY/RUB.
    Для **снятия**: отклонение эффективного RUB/THB от оплаты в том же канале.
    """
    print(
        build_rshb_text(
            thb_nets=(thb_net,),
            atm_fee_thb=atm_fee_thb,
            on=on,
            moex_override=moex_override,
            rub_card_atm_pct=rub_card_atm_pct,
            issuer_cny_atm_pct=issuer_cny_atm_pct,
        ),
        end="",
    )


def demo_report(
    thb: float = 30_000.0,
    atm_fee: float = 250.0,
    on: Optional[date] = None,
) -> None:
    cny_per_thb, moex, rshb_sell_dec, _rd, online_sell_dec, _rod, stale, _ = (
        fetch_live_inputs(on)
    )
    if stale:
        print(
            "⚠ Таймаут сети: использованы последние сохранённые курсы "
            f"({LIVE_INPUTS_CACHE_FILE.name}).",
            file=sys.stderr,
        )
    rshb_sell = float(rshb_sell_dec)
    chans = default_channels(
        moex_cny_rub=moex,
        rshb_cny_rur_sell=rshb_sell,
        rshb_app_cny_rub=float(online_sell_dec),
    )

    print("=== Входные данные ===")
    print(f"Дата UnionPay/MOEX: {on or 'сегодня'}")
    print(f"1 THB = {cny_per_thb:.8f} CNY (UnionPay)")
    print(f"CNY/RUB MOEX (CNYRUB_TOM): {moex:.6f}")
    print(f"РСХБ CNY/RUR продажа (offline, руб. карта): {rshb_sell:.4f}")
    print(f"РСХБ CNY/RUR продажа (online, приложение): {float(online_sell_dec):.4f}")
    print()

    print("=== Курс THB/RUB, оплата (без комиссии эмитента на CNY) ===")
    for c in chans:
        r = rub_per_thb(cny_per_thb, c.cny_rub)
        print(f"  {r:.3f} RUB/THB | {c.name}")
    print()

    print(f"=== Снятие {thb:g} THB, комиссия банкомата {atm_fee:g} THB ===")
    print("(юаневая карта: +3% к базе в CNY; курс в строке = RUB / нетто THB)")
    for c in chans[:3]:
        rub, per = atm_rub_from_cny_path(
            thb, atm_fee, cny_per_thb, c.cny_rub, issuer_fee_on_cny_base=0.03
        )
        print(f"  {per:.3f} RUB/THB | {rub:,.2f} RUB | {c.name}")
    # Рублёвая карта: без +3% к базе в CNY; только CNY→RUB по продаже + комиссия эмитента в RUB.
    rub_rubcard, per_rc = atm_rub_from_cny_path(
        thb,
        atm_fee,
        cny_per_thb,
        chans[-1].cny_rub,
        issuer_fee_on_cny_base=0.0,
        extra_rub_pct_of_base=0.01,
    )
    print(
        f"  {per_rc:.3f} RUB/THB | {rub_rubcard:,.2f} RUB | "
        f"РСХБ RUB (без 3% на CNY) + 1% к базе в RUB"
    )
    print()

    print("=== Пороги THB (комиссия % вместо фикса) ===")
    t_cny = min_thb_for_cny_percent_fee(17.0, 0.03, cny_per_thb, atm_fee)
    rub_at = (t_cny + atm_fee) * cny_per_thb * (1.03) * chans[0].cny_rub
    print(f"  Юаневая (3% CNY vs 17 CNY): min нетто THB ≈ {t_cny:.0f} (~{rub_at:,.2f} RUB по MOEX)")
    t_rub = min_thb_for_rub_percent_fee(199.0, 0.01, cny_per_thb, rshb_sell, atm_fee)
    rub_at_r = (t_rub + atm_fee) * cny_per_thb * rshb_sell
    print(f"  Рублевая (1% RUB vs 199 RUB): min нетто THB ≈ {t_rub:.0f} (~{rub_at_r:,.2f} RUB база до %)")

    print()
    print("=== 30 000 THB в RUB через CNY (MOEX, приложение, АТБ CNY) ===")
    for c in chans[:3]:
        total = payment_rub(thb, cny_per_thb, c.cny_rub)
        print(f"  {total:,.2f} RUB | {c.name} | 1 THB = {total/thb:.3f} RUB")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Калькулятор THB/RUB/CNY UnionPay+MOEX+РСХБ")
    p.add_argument("--date", help="YYYY-MM-DD (UnionPay файл + таблица РСХБ)", default=None)
    p.add_argument("--thb", type=float, default=30_000.0)
    p.add_argument("--atm-fee", type=float, default=250.0)
    p.add_argument("--moex-override", type=float, default=None, help="Подставить MOEX вручную")
    p.add_argument(
        "--example1",
        action="store_true",
        help="Вывод как в примере 1): оплата + снятие, эмодзи и проценты",
    )
    return p


def cli_main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    d = date.fromisoformat(args.date) if args.date else None
    if getattr(args, "example1", False):
        report_example1(
            thb_net=args.thb,
            atm_fee_thb=args.atm_fee,
            on=d,
            moex_override=args.moex_override,
        )
        return 0
    if args.moex_override is not None:
        cpt, _, rsd, _, online_sd, _, stale, _ = fetch_live_inputs(
            d, moex_override=args.moex_override
        )
        if stale:
            print(
                "⚠ Таймаут сети: использованы последние сохранённые курсы "
                f"({LIVE_INPUTS_CACHE_FILE.name}).",
                file=sys.stderr,
            )
        moex = args.moex_override
        chans = default_channels(
            moex_cny_rub=moex,
            rshb_cny_rur_sell=float(rsd),
            rshb_app_cny_rub=float(online_sd),
        )
        print("MOEX (ручной ввод для сравнения с вашим примером):", moex)
        print("1 THB =", cpt, "CNY (UnionPay на выбранную дату)")
        for c in chans:
            print(f"  {rub_per_thb(cpt, c.cny_rub):.4f} RUB/THB | {c.name}")
        return 0
    demo_report(thb=args.thb, atm_fee=args.atm_fee, on=d)
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
