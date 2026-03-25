#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bybit P2P: минимальная цена покупки USDT за RUB по объявлениям «онлайн».

Два сценария (как на сайте):
  A) есть способ **Cash Deposit to Bank** (payment id **18**);
  B) есть **Bank Transfer** (**14**), но **нет** Cash Deposit to Bank (**18**).

В запросе: ``vaMaker`` и ``canTrade`` (как в веб-форме Verified / Eligible).
Completion rate: ``recentExecuteRate >= min_completion`` (по умолчанию 99).

Доп. отбор (как на HTX для 100 USDT): ``lastQuantity >= target_usdt`` и
``minAmount >= target_usdt * price`` (фиатные лимиты в ответе API — в RUB).

Публичные POST без API-ключа (как у сайта).

Если ответ **403** с HTML «Access Denied» / ``edgesuite.net``: у CDN включена защита;
нужны заголовки как у браузера (ниже в ``DEFAULT_HEADERS``). С IP датацентра или при
жёсткой проверке TLS всё равно может блокировать — тогда другая сеть или HTTP-клиент
с отпечатком Chrome (например ``curl_cffi`` / запуск с домашнего IP).

При ``Accept-Encoding: …, br`` ответ может быть в Brotli — тогда нужен пакет ``brotli``
(``pip install brotli``); иначе обычно приходит gzip и хватает стандартной библиотеки.

Если установлен **curl-cffi** (см. ``requirements.txt`` / Docker-образ), запросы идут через него
с TLS как у Chrome; по умолчанию перебираются профили ``chrome131``, ``chrome124``, ``chrome120``.
В контейнере без ``curl-cffi`` запрос падает с явной подсказкой ``docker compose build``, а не
тихо уходит в ``urllib`` (403). Env: ``BYBIT_IMPERSONATE_TRY``, ``BYBIT_ALLOW_URLLIB_IN_DOCKER=1``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

QUERY_PAYMENTS_URL = "https://www.bybit.com/x-api/fiat/otc/configuration/queryAllPaymentList"
ITEM_ONLINE_URL = "https://www.bybit.com/x-api/fiat/otc/item/online"

# RUB fiat payment ids (справочник на сайте)
PAYMENT_BANK_TRANSFER = "14"
PAYMENT_CASH_DEPOSIT_BANK = "18"

DEFAULT_TARGET_USDT = 100.0

# Как у Chrome на /fiat/trade/otc/ (проверено curl: без Sec-Fetch/sec-ch-ua часто 403 Akamai).
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
    # Как в браузере; br см. :func:`_decompress_body`
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Origin": "https://www.bybit.com",
    "Referer": "https://www.bybit.com/fiat/trade/otc/?actionType=0",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# curl_cffi: имперсонация TLS/JA3 (см. документацию curl_cffi). Несколько имён через запятую —
# перебор при 403 от Akamai. Переопределение: BYBIT_IMPERSONATE_TRY.
_CURL_CFFI_IMPERSONATE_TRY: Tuple[str, ...] = tuple(
    x.strip()
    for x in os.environ.get("BYBIT_IMPERSONATE_TRY", "chrome131,chrome124,chrome120").split(",")
    if x.strip()
)


def _decompress_body(blob: bytes, content_encoding: str) -> bytes:
    ce = (content_encoding or "").lower()
    if not ce or "identity" in ce:
        return blob
    if "gzip" in ce:
        return gzip.decompress(blob)
    if "br" in ce:
        try:
            import brotli  # type: ignore[import-untyped]
        except ImportError:
            raise RuntimeError(
                "Ответ Bybit сжат Brotli (br). Установите: pip install brotli"
            ) from None
        return brotli.decompress(blob)
    if "deflate" in ce:
        import zlib

        try:
            return zlib.decompress(blob, -zlib.MAX_WBITS)
        except zlib.error:
            return zlib.decompress(blob)
    return blob


def _read_json_response(resp: Any) -> str:
    blob = resp.read()
    blob = _decompress_body(blob, resp.headers.get("Content-Encoding") or "")
    return blob.decode(resp.headers.get_content_charset() or "utf-8", errors="replace")


def _akamai_403_hint(err_body: str) -> str:
    if "Access Denied" in err_body or "edgesuite" in err_body.lower():
        return (
            " (Akamai 403: TLS как у Python; установите curl-cffi: pip install curl-cffi "
            "или запускайте образ из docker-compose с Dockerfile проекта)"
        )
    return ""


def _post_json_curl_cffi(url: str, body: Any, *, timeout: float) -> Dict[str, Any]:
    from curl_cffi import requests as curl_requests

    hdrs = dict(DEFAULT_HEADERS)
    last_err: Optional[RuntimeError] = None
    for imp in _CURL_CFFI_IMPERSONATE_TRY:
        r = curl_requests.post(
            url,
            json=body,
            headers=hdrs,
            impersonate=imp,
            timeout=timeout,
        )
        if r.status_code == 200:
            try:
                return r.json()
            except json.JSONDecodeError as e:
                raise RuntimeError(f"Не JSON от {url}: {r.text[:500]!r}") from e
        hint = _akamai_403_hint(r.text or "")
        last_err = RuntimeError(f"HTTP {r.status_code} для {url} (impersonate={imp!r}){hint}")
        if r.status_code == 403 and hint:
            continue
        raise last_err
    if last_err:
        raise last_err
    raise RuntimeError(f"Пустой список BYBIT_IMPERSONATE_TRY для {url}")


def _post_json_urllib(url: str, body: Any, *, timeout: float) -> Dict[str, Any]:
    ctx = ssl.create_default_context()
    data = json.dumps(body, separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=dict(DEFAULT_HEADERS), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            text = _read_json_response(resp)
    except urllib.error.HTTPError as e:
        hint = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            hint = _akamai_403_hint(err_body)
        except OSError:
            pass
        raise RuntimeError(f"HTTP {e.code} для {url}{hint}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Сеть: {e}") from e
    return json.loads(text)


def _in_container() -> bool:
    return os.path.isfile("/.dockerenv")


def post_json(url: str, body: Any, *, timeout: float = 60.0) -> Dict[str, Any]:
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        if _in_container() and os.environ.get("BYBIT_ALLOW_URLLIB_IN_DOCKER") != "1":
            raise RuntimeError(
                "В Docker для Bybit x-api нужен пакет curl-cffi (TLS как у браузера). "
                "Образ должен собираться из Dockerfile проекта: "
                "`docker compose build rates-api`. "
                "Временный обход (не рекомендуется): BYBIT_ALLOW_URLLIB_IN_DOCKER=1"
            ) from None
        return _post_json_urllib(url, body, timeout=timeout)
    return _post_json_curl_cffi(url, body, timeout=timeout)


def fetch_payment_list() -> Dict[str, Any]:
    """Справочник способов оплаты (POST body ``{}``)."""
    out = post_json(QUERY_PAYMENTS_URL, {})
    if out.get("ret_code") not in (0, "0", None):
        raise RuntimeError(f"queryAllPaymentList: {out.get('ret_msg')!r} ({out!r})")
    return out.get("result") or {}


def build_online_body(
    *,
    page: int,
    size: int,
    verification_filter: int,
    side: str = "1",
) -> Dict[str, Any]:
    """Тело как у веб-клиента: покупка USDT за RUB (side=1)."""
    return {
        "userId": "",
        "tokenId": "USDT",
        "currencyId": "RUB",
        "payment": [],
        "side": side,
        "size": str(size),
        "page": str(page),
        "amount": "",
        "vaMaker": True,
        "bulkMaker": False,
        "canTrade": True,
        "verificationFilter": verification_filter,
        "sortType": "OVERALL_RANKING",
        "paymentPeriod": [],
        "itemRegion": 1,
    }


def fetch_all_online_items(
    *,
    size: int = 20,
    verification_filter: int = 0,
    side: str = "1",
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    first = post_json(ITEM_ONLINE_URL, build_online_body(page=1, size=size, verification_filter=verification_filter, side=side))
    if first.get("ret_code") not in (0, "0", None):
        raise RuntimeError(f"item/online: {first.get('ret_msg')!r}")
    result = first.get("result") or {}
    count = int(result.get("count") or 0)
    items: List[Dict[str, Any]] = list(result.get("items") or [])
    if count <= 0:
        return items
    total_pages = max(1, math.ceil(count / size))
    if max_pages is not None:
        total_pages = min(total_pages, max_pages)
    for page in range(2, total_pages + 1):
        nxt = post_json(
            ITEM_ONLINE_URL,
            build_online_body(page=page, size=size, verification_filter=verification_filter, side=side),
        )
        if nxt.get("ret_code") not in (0, "0", None):
            raise RuntimeError(f"item/online page {page}: {nxt.get('ret_msg')!r}")
        chunk = list((nxt.get("result") or {}).get("items") or [])
        items.extend(chunk)
        if not chunk:
            break
    return items


def payment_ids(item: Dict[str, Any]) -> List[str]:
    return [str(x) for x in (item.get("payments") or [])]


def item_tradable_token_quantity(item: Dict[str, Any]) -> float:
    """Доступно USDT к продаже (``lastQuantity``, иначе ``quantity``)."""
    for key in ("lastQuantity", "quantity"):
        raw = item.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return 0.0


def item_passes_target_usdt_filters(
    item: Dict[str, Any],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> bool:
    """Как на HTX: объём USDT в объявлении и мин. сделка в RUB на ``target_usdt`` USDT."""
    try:
        if item_tradable_token_quantity(item) < float(target_usdt):
            return False
        price = float(item.get("price"))
        min_amt = float(item.get("minAmount"))
    except (TypeError, ValueError):
        return False
    if price <= 0:
        return False
    return min_amt >= float(target_usdt) * price


def filter_by_target_usdt(
    items: List[Dict[str, Any]],
    *,
    target_usdt: float = DEFAULT_TARGET_USDT,
) -> List[Dict[str, Any]]:
    return [it for it in items if item_passes_target_usdt_filters(it, target_usdt=target_usdt)]


def completion_ok(item: Dict[str, Any], min_completion: float) -> bool:
    try:
        return float(item.get("recentExecuteRate")) >= min_completion
    except (TypeError, ValueError):
        return False


def filter_cash_deposit_to_bank(items: List[Dict[str, Any]], min_completion: float) -> List[Dict[str, Any]]:
    """A: есть 18, completion >= порога."""
    out = []
    for it in items:
        p = payment_ids(it)
        if PAYMENT_CASH_DEPOSIT_BANK in p and completion_ok(it, min_completion):
            out.append(it)
    return out


def filter_bank_transfer_no_cash(items: List[Dict[str, Any]], min_completion: float) -> List[Dict[str, Any]]:
    """B: есть 14, нет 18, completion >= порога."""
    out = []
    for it in items:
        p = payment_ids(it)
        if PAYMENT_BANK_TRANSFER in p and PAYMENT_CASH_DEPOSIT_BANK not in p and completion_ok(it, min_completion):
            out.append(it)
    return out


def min_by_price(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
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


@dataclass
class BestQuote:
    label: str
    item: Optional[Dict[str, Any]]
    min_price: Optional[float]
    matched_count: int


def summarize(label: str, items: List[Dict[str, Any]]) -> BestQuote:
    best = min_by_price(items)
    mp = float(best["price"]) if best else None
    return BestQuote(label=label, item=best, min_price=mp, matched_count=len(items))


def item_brief(it: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "nickName": it.get("nickName"),
        "price": it.get("price"),
        "payments": payment_ids(it),
        "recentExecuteRate": it.get("recentExecuteRate"),
        "minAmount": it.get("minAmount"),
        "maxAmount": it.get("maxAmount"),
        "lastQuantity": it.get("lastQuantity"),
        "quantity": it.get("quantity"),
        "id": it.get("id"),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Bybit P2P: лучшие цены USDT/RUB (два фильтра по способам оплаты)")
    ap.add_argument("--min-completion", type=float, default=99.0, metavar="PCT", help="Мин. recentExecuteRate (по умолчанию 99)")
    ap.add_argument(
        "--target-usdt",
        type=float,
        default=DEFAULT_TARGET_USDT,
        metavar="X",
        dest="target_usdt",
        help=(
            f"После загрузки: lastQuantity ≥ X и minAmount ≥ X·price (RUB). "
            f"По умолчанию {DEFAULT_TARGET_USDT:g}"
        ),
    )
    ap.add_argument(
        "--min-usdt",
        type=float,
        default=None,
        metavar="X",
        dest="legacy_min_usdt",
        help="Синоним --target-usdt (если задан, переопределяет)",
    )
    ap.add_argument("--size", type=int, default=20, help="Размер страницы item/online")
    ap.add_argument(
        "--verification-filter",
        type=int,
        default=0,
        help="Поле verificationFilter в теле запроса (как в браузере; 0 — типичное значение)",
    )
    ap.add_argument("--payments-only", action="store_true", help="Только запрос справочника способов оплаты")
    ap.add_argument("--json", action="store_true", help="Вывод в JSON")
    ap.add_argument("--max-pages", type=int, default=None, help="Ограничить число страниц (отладка)")
    return ap


def cli_main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    target_usdt = (
        float(args.legacy_min_usdt)
        if args.legacy_min_usdt is not None
        else float(args.target_usdt)
    )

    try:
        if args.payments_only:
            plist = fetch_payment_list()
            if args.json:
                print(json.dumps(plist, ensure_ascii=False, indent=2))
            else:
                print(json.dumps(plist, ensure_ascii=False, indent=2)[:4000])
                if len(json.dumps(plist)) > 4000:
                    print("\n… (усечено, используйте --json)", file=sys.stderr)
            return 0

        items = fetch_all_online_items(
            size=args.size,
            verification_filter=args.verification_filter,
            max_pages=args.max_pages,
        )
        total_before = len(items)
        items = filter_by_target_usdt(items, target_usdt=target_usdt)
        a_items = filter_cash_deposit_to_bank(items, args.min_completion)
        b_items = filter_bank_transfer_no_cash(items, args.min_completion)
        qa = summarize("Cash Deposit to Bank (18)", a_items)
        qb = summarize("Bank Transfer без Cash Deposit (14, без 18)", b_items)

        if args.json:
            out = {
                "total_items_fetched": total_before,
                "after_target_usdt_filter": len(items),
                "target_usdt": target_usdt,
                "min_completion": args.min_completion,
                "verification_filter": args.verification_filter,
                "cash_deposit_to_bank": {
                    "matched": qa.matched_count,
                    "min_price": qa.min_price,
                    "best": item_brief(qa.item) if qa.item else None,
                },
                "bank_transfer_only": {
                    "matched": qb.matched_count,
                    "min_price": qb.min_price,
                    "best": item_brief(qb.item) if qb.item else None,
                },
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0

        print(
            f"Загружено объявлений: {total_before}; после фильтра "
            f"lastQuantity≥{target_usdt:g} и minAmount≥{target_usdt:g}·price: {len(items)} "
            f"(completion ≥ {args.min_completion:g} % дальше по сценариям)"
        )
        print()
        for q in (qa, qb):
            print(f"=== {q.label} ===")
            print(f"  Подошло объявлений: {q.matched_count}")
            if q.item is None:
                print("  Минимальная цена: —")
            else:
                it = q.item
                print(f"  Минимальная цена: {q.min_price:g} RUB за 1 USDT")
                print(f"  Продавец: {it.get('nickName')}")
                print(f"  payments: {payment_ids(it)}")
                print(f"  recentExecuteRate: {it.get('recentExecuteRate')}")
                print(f"  minAmount / maxAmount: {it.get('minAmount')} / {it.get('maxAmount')}")
            print()
        return 0
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
