#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
По расписанию (cron): проверить unified-кеш на истечение TTL и прогреть истёкшие блоки.

Файл кеша: ``RATES_UNIFIED_CACHE_FILE`` или ``.rates_unified_cache.json`` в корне репозитория
(см. :mod:`rates_unified_cache`).

Пример crontab (каждые 10 минут, из корня репо)::

    */10 * * * * cd /path/to/rates-api && /usr/bin/python3.9 cron/refresh_expired_unified_cache.py >> /var/log/rates-cache-refresh.log 2>&1

Опции::

    --dry-run   только показать, что истекло и что было бы обновлено, без сети.

Почему раньше cash/exchange могли запускаться **каждый раз**: в ``l2`` остаются записи с
другими параметрами кеша (другой ``timeout``/валюта, либо ``l2:cash:…:city:…`` после
``/cash N``). Прогрев из этого скрипта перезаписывает только «канонические'' L2 под те же
параметры, что и вызовы ниже; старые ключи TTL не обновляют и вечно числятся истёкшими.
Для **exchange** L2 не зависит от ``top_n`` (один снимок на lang/timeout/fiat; в тексте до
200 филиалов); старые ключи ``l2:exchange:*`` с ``top_n`` в дайджесте со временем протухнут.
По L1 — любые ``ex:l1:*``.

Для **cash** по L2 — только канонический ключ полного отчёта (без ``:city:``). По L1 **не**
смотрим ``cash:l1:*`` / ``cash_thb:l1:cell:*``: в файле часто остаются «осиротевшие'' ячейки
(другой город, старый формат ключа), они больше никогда не перезаписываются и вечно
истекают — из‑за них cash вызывался бы каждый раз. Исключение: общий ``cash_thb:l1:tt``
(курсы TT для наличного отчёта) — если он протух, прогрев нужен.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_loader import load_repo_dotenv

load_repo_dotenv(_ROOT)

import rates_unified_cache as ucc
from cash_report import _cash_l2_key
from exchange_report import _ex_l2_key

logger = logging.getLogger(__name__)

# Должны совпадать с параметрами прогрева ниже (get_exchange_text / get_cash_text).
_CRON_EXCHANGE_TOP_N = 10
_CRON_EXCHANGE_LANG = "ru"
_CRON_EXCHANGE_TIMEOUT = 28.0
_CRON_CASH_TOP_N = 20
_CRON_CASH_USE_BANKI = True
_CRON_CASH_TIMEOUT = 22.0
_CRON_CASH_KIND = "plain_tt"

L2_EXCHANGE_CANONICAL = _ex_l2_key(
    lang=_CRON_EXCHANGE_LANG,
    timeout=_CRON_EXCHANGE_TIMEOUT,
)
L2_CASH_CANONICAL = _cash_l2_key(
    kind=_CRON_CASH_KIND,
    top_n=_CRON_CASH_TOP_N,
    use_rbc=True,
    use_banki=_CRON_CASH_USE_BANKI,
    use_vbr=True,
    timeout=_CRON_CASH_TIMEOUT,
)

# Общий L1 для карт TT (plain cash и cash-thb), см. cash_report.build_cash_report_text.
CASH_TT_L1_KEY = "cash_thb:l1:tt"


def _is_expired(ent: Any, now: float) -> bool:
    if not isinstance(ent, dict):
        return False
    saved = float(ent.get("saved_unix") or 0)
    ttl = int(ent.get("ttl_sec") or 0)
    if ttl <= 0:
        ttl = 60
    if saved <= 0:
        return True
    return (now - saved) > ttl


def _collect_expired(doc: Dict[str, Any], now: float) -> Tuple[List[str], List[str], List[str]]:
    l1 = doc.get("l1") or {}
    l2 = doc.get("l2") or {}
    prim = doc.get("prim") or {}
    e1 = [k for k, e in l1.items() if _is_expired(e, now)]
    e2 = [k for k, e in l2.items() if _is_expired(e, now)]
    ep = [k for k, e in prim.items() if _is_expired(e, now)]
    return sorted(e1), sorted(e2), sorted(ep)


def _plan_refresh(
    expired_l1: List[str], expired_l2: List[str], expired_prim: List[str]
) -> Set[str]:
    """Имена задач: summary, usdt, exchange, cash."""
    need: Set[str] = set()
    for k in expired_l1:
        if k.startswith("rs:"):
            need.add("summary")
        elif k.startswith("usdt:l1:"):
            need.add("usdt")
        elif k.startswith("ex:l1:"):
            need.add("exchange")
        elif k == CASH_TT_L1_KEY:
            need.add("cash")
        # cash:l1:* / cash_thb:l1:cell:* не используем — часто осиротевшие ключи (см. docstring).
        # chatcash:* — снимок userbot; обновление только из userbot, здесь не трогаем.
    for _k in expired_prim:
        need.add("summary")
    for k in expired_l2:
        if k.startswith("l2:summary:"):
            need.add("summary")
        elif k.startswith("l2:usdt:"):
            need.add("usdt")
        elif k == L2_EXCHANGE_CANONICAL:
            need.add("exchange")
        elif k == L2_CASH_CANONICAL:
            need.add("cash")
    return need


def main(argv: List[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    p = argparse.ArgumentParser(description="Обновить истёкшие записи unified-кеша.")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Не вызывать обновления, только показать план",
    )
    args = p.parse_args(argv)

    path = ucc.DEFAULT_UNIFIED_CACHE_PATH
    doc = ucc.load_unified(path)
    now = time.time()
    expired_l1, expired_l2, expired_prim = _collect_expired(doc, now)
    if not expired_l1 and not expired_l2 and not expired_prim:
        logger.info("TTL: истёкших записей нет (%s)", path)
        return 0

    logger.info(
        "Истекло L1: %d, L2: %d, prim: %d",
        len(expired_l1),
        len(expired_l2),
        len(expired_prim),
    )
    if len(expired_l1) <= 30:
        for k in expired_l1:
            logger.info("  L1 %s", k)
    else:
        for k in expired_l1[:15]:
            logger.info("  L1 %s", k)
        logger.info("  L1 … ещё %d ключей", len(expired_l1) - 15)
    if len(expired_l2) <= 20:
        for k in expired_l2:
            logger.info("  L2 %s", k)
    else:
        for k in expired_l2[:10]:
            logger.info("  L2 %s", k)
        logger.info("  L2 … ещё %d ключей", len(expired_l2) - 10)

    orphan_x = sum(
        1 for k in expired_l2 if k.startswith("l2:exchange:") and k != L2_EXCHANGE_CANONICAL
    )
    orphan_c = sum(
        1 for k in expired_l2 if k.startswith("l2:cash:") and k != L2_CASH_CANONICAL
    )
    if orphan_x or orphan_c:
        logger.info(
            "Игнор для плана прогрева: истёкших чужих L2 exchange=%d, cash=%d "
            "(старые top_n/timeout или :city:)",
            orphan_x,
            orphan_c,
        )

    orphan_cash_l1 = sum(
        1
        for k in expired_l1
        if k.startswith("cash:l1:")
        or (k.startswith("cash_thb:l1:") and k != CASH_TT_L1_KEY)
    )
    if orphan_cash_l1:
        logger.info(
            "Игнор для плана прогрева cash: истёкших ячеек L1 cash/cash_thb=%d "
            "(не триггер — см. docstring)",
            orphan_cash_l1,
        )

    if expired_prim and len(expired_prim) <= 20:
        for k in expired_prim:
            logger.info("  prim %s", k)
    elif expired_prim:
        for k in expired_prim[:10]:
            logger.info("  prim %s", k)
        logger.info("  prim … ещё %d ключей", len(expired_prim) - 10)

    tasks = _plan_refresh(expired_l1, expired_l2, expired_prim)
    if not tasks:
        logger.info("Нет задач прогрева (например, только chatcash:*). Выход.")
        return 0

    logger.info("План обновления: %s", ", ".join(sorted(tasks)))
    if args.dry_run:
        return 0

    from bot.summary_adapter import (
        get_cash_text,
        get_exchange_text,
        get_summary_text,
        get_usdt_text,
    )

    errors = 0
    # Для summary используем refresh=True, чтобы исключить fallback в legacy
    # .rates_summary_cache.json и гарантированно пересобирать строки в unified.
    # Для остальных отчётов оставляем мягкий прогрев по unified TTL/deps.
    order = ("summary", "usdt", "exchange", "cash")
    for name in order:
        if name not in tasks:
            continue
        try:
            logger.info("Обновление кеша: старт %s", name)
            if name == "summary":
                get_summary_text(refresh=True, unified_allow_stale=False)
            elif name == "usdt":
                get_usdt_text(refresh=False, unified_allow_stale=False)
            elif name == "exchange":
                get_exchange_text(
                    refresh=False,
                    unified_allow_stale=False,
                    top_n=_CRON_EXCHANGE_TOP_N,
                    lang=_CRON_EXCHANGE_LANG,
                )
            elif name == "cash":
                get_cash_text(
                    refresh=False,
                    unified_allow_stale=False,
                    top_n=20,
                    city_label="",
                )
            logger.info("Обновление кеша: готово %s", name)
        except Exception:
            logger.exception("Ошибка при обновлении %s", name)
            errors += 1

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
