#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HTX (Huobi) OTC P2P: объявления USDT/RUB через публичный GET (как веб-клиент).

Эндпоинт::

    GET https://www.htx.com/-/x/otc/v1/data/trade-market?...

Параметры — как при фильтрах на сайте (``tradeType=sell``, мерчанты, онлайн,
``makerCompleteRate``, ...). См. :func:`default_trade_market_params`.

Отбор:
  • ликвидность USDT: ``tradeCount >= target_usdt``;
  • мин. сделка в RUB: ``minTradeLimit >= target_usdt * price``;
  • «наличные»: ``payMethodId`` из :data:`CASH_PAY_METHOD_IDS` и/или имя способа
    по :data:`CASH_NAME_RE` (Cash Deposit, Cash in Person, наличн..., и т.п.).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

TRADE_MARKET_URL = "https://www.htx.com/-/x/otc/v1/data/trade-market"

# Подтверждено выборкой по USDT/RUB + расширяемо через :data:`CASH_NAME_RE`.
CASH_PAY_METHOD_IDS = frozenset({21, 169})

_CASH_NAME_RE = re.compile(
    r"(?i)"
    r"(наличн"
    r"|cash\s*deposit"
    r"|cash\s*in\s*person"
    r"|личн(ая)?\s*встреч"
    r"|встреч[аи]\s+.*налич"
    r"|face\s*to\s*face)",
)

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

DEFAULT_TARGET_USDT = 100.0


def default_trade_market_params(*, curr_page: int) -> Dict[str, str]:
    """Параметры запроса как у пользовательских фильтров (страница подставляется)."""
    return {
        "coinId": "2",
        "currency": "11",
        "tradeType": "sell",
        "currPage": str(curr_page),
        "payMethod": "",
        "acceptOrder": "0",
        "country": "",
        "blockType": "general",
        "online": "1",
        "range": "0",
        "amount": "",
        "isThumbsUp": "false",
        "isMerchant": "true",
        "isTraded": "false",
        "onlyTradable": "true",
        "isFollowed": "false",
        "makerCompleteRate": "90",
        "brandLabelIds": "",
    }


def _get_json(url: str, *, timeout: float = 60.0) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=dict(HEADERS), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} для {url}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(str(e)) from e
    return json.loads(raw)


def fetch_trade_market_page(curr_page: int, *, timeout: float = 60.0) -> Dict[str, Any]:
    q = urllib.parse.urlencode(default_trade_market_params(curr_page=curr_page))
    return _get_json(f"{TRADE_MARKET_URL}?{q}", timeout=timeout)


def fetch_all_offers(
    *,
    max_pages: Optional[int] = 30,
    timeout: float = 60.0,
) -> List[Dict[str, Any]]:
    """Все объявления с пагинации (пока есть data или не вышли за totalPage)."""
    first = fetch_trade_market_page(1, timeout=timeout)
    if first.get("code") != 200:
        raise RuntimeError(first.get("message") or f"HTX OTC: {first!r}")
    out: List[Dict[str, Any]] = list(first.get("data") or [])
    try:
        total_page = int(first.get("totalPage") or 1)
    except (TypeError, ValueError):
        total_page = 1
    if max_pages is not None:
        total_page = min(total_page, max_pages)
    for page in range(2, total_page + 1):
        nxt = fetch_trade_market_page(page, timeout=timeout)
        if nxt.get("code") != 200:
            raise RuntimeError(nxt.get("message") or f"HTX OTC page {page}: {nxt!r}")
        chunk = list(nxt.get("data") or [])
        if not chunk:
            break
        out.extend(chunk)
    return out


def pay_method_ids_from_field(row: Dict[str, Any]) -> List[int]:
    raw = row.get("payMethod")
    if raw is None or raw == "":
        return []
    if isinstance(raw, int):
        return [raw]
    out: List[int] = []
    for p in str(raw).split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def pay_method_entry_is_cash(pm: Dict[str, Any]) -> bool:
    try:
        pid = int(pm.get("payMethodId"))
    except (TypeError, ValueError):
        pid = None
    if pid is not None and pid in CASH_PAY_METHOD_IDS:
        return True
    name = str(pm.get("name") or "")
    return bool(_CASH_NAME_RE.search(name))


def row_has_cash(row: Dict[str, Any]) -> bool:
    for pm in row.get("payMethods") or []:
        if not isinstance(pm, dict):
            continue
        if pay_method_entry_is_cash(pm):
            return True
    for pid in pay_method_ids_from_field(row):
        if pid in CASH_PAY_METHOD_IDS:
            return True
    return False


def row_trade_count_ok(
    row: Dict[str, Any],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> bool:
    try:
        return float(row.get("tradeCount") or 0) >= float(target_usdt)
    except (TypeError, ValueError):
        return False


def row_rub_limits_allow_target_usdt(
    row: Dict[str, Any],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> bool:
    """Минимальная сделка в RUB не ниже стоимости ``target_usdt`` USDT по ``price``."""
    try:
        price = float(row.get("price"))
        min_rub = float(row.get("minTradeLimit"))
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    rub_for_target = float(target_usdt) * price
    return min_rub >= rub_for_target


def row_passes_target_usdt_filters(
    row: Dict[str, Any],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> bool:
    return row_trade_count_ok(row, target_usdt=target_usdt) and row_rub_limits_allow_target_usdt(
        row, target_usdt=target_usdt
    )


def min_by_price(items: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not items:
        return None
    best: Optional[Dict[str, Any]] = None
    best_price = math.inf
    for it in items:
        try:
            px = float(it.get("price"))
        except (TypeError, ValueError):
            continue
        if px < best_price:
            best_price = px
            best = it
    return best


def partition_cash_non_cash(
    rows: Sequence[Dict[str, Any]],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """После фильтра по объёму USDT и minTradeLimit — две корзины: наличные / без наличных."""
    liquid = [r for r in rows if row_passes_target_usdt_filters(r, target_usdt=target_usdt)]
    with_cash = [r for r in liquid if row_has_cash(r)]
    without = [r for r in liquid if not row_has_cash(r)]
    return with_cash, without


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="HTX OTC: USDT/RUB (trade-market) + фильтры")
    p.add_argument(
        "--json",
        action="store_true",
        help="Сводка по отфильтрованным объявлениям (stdout JSON)",
    )
    p.add_argument(
        "--max-pages",
        type=int,
        default=30,
        metavar="N",
        help="Максимум страниц пагинации (по умолчанию 30)",
    )
    p.add_argument(
        "--target-usdt",
        type=float,
        default=DEFAULT_TARGET_USDT,
        metavar="X",
        dest="target_usdt",
        help=(
            f"Целевой объём USDT: tradeCount ≥ X и minTradeLimit ≥ X·price (RUB) "
            f"(по умолчанию {DEFAULT_TARGET_USDT:g})"
        ),
    )
    p.add_argument(
        "--min-usdt",
        type=float,
        default=None,
        metavar="X",
        dest="legacy_min_usdt",
        help="Синоним --target-usdt (если задан, переопределяет)",
    )
    return p


def cli_main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_usdt = (
        float(args.legacy_min_usdt)
        if args.legacy_min_usdt is not None
        else float(args.target_usdt)
    )
    try:
        rows = fetch_all_offers(max_pages=args.max_pages)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    wc, wo = partition_cash_non_cash(rows, target_usdt=target_usdt)
    bcash = min_by_price(wc)
    bno = min_by_price(wo)
    if args.json:
        out = {
            "matched": {
                "cash": len(wc),
                "non_cash": len(wo),
            },
            "best": {
                "cash": {"price": float(bcash["price"]), "id": bcash.get("id")} if bcash else None,
                "non_cash": {"price": float(bno["price"]), "id": bno.get("id")} if bno else None,
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
    print(f"Объявлений всего (снято с API): {len(rows)}")
    print(
        f"С tradeCount >= {target_usdt:g} и minTradeLimit >= {target_usdt:g}·price: "
        f"наличные {len(wc)}, без наличных {len(wo)}"
    )
    if bcash:
        print(f"  мин. цена (наличные):     {bcash.get('price')} RUB/USDT  id={bcash.get('id')}")
    if bno:
        print(f"  мин. цена (без наличных): {bno.get('price')} RUB/USDT  id={bno.get('id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli_main())
