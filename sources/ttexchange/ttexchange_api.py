#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Клиент к публичным JSON API, которые использует сайт ttexchange.com (Angular-приложение).

ВАЖНО
------
Это не опубликованная официальная документация: URL и параметры восстановлены из фронтенда.
Сервер может изменить формат ответа или отключить эндпоинты без предупреждения.

Два базовых хоста (как в коде приложения):
  * PUBLIC_API  — контент сайта: филиалы, баннеры, новости, «Паттайя», сейфы и т.д.
  * RATES_API   — курсы валют и история (отдельный сервис).

Пример запуска из командной строки::

    python ttexchange_api.py rates --branch 3
    python ttexchange_api.py stores
    python ttexchange_api.py faqs
"""

from __future__ import annotations

import argparse
import json
import logging
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Union

from rates_http import urlopen_retriable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Константы: базовые URL (источник — минифицированный main.*.js сайта).
# ---------------------------------------------------------------------------

PUBLIC_API = "https://api.ttexchange.com"
RATES_API = "https://api.software.ttexchange.com"

DEFAULT_LANG = "en"
DEFAULT_TIMEOUT = 30
DEFAULT_USER_AGENT = "ttexchange-api-client/1.0 (python)"


# ---------------------------------------------------------------------------
# Низкоуровневый HTTP + разбор JSON
# ---------------------------------------------------------------------------


def _build_url(base: str, path: str, params: Optional[Mapping[str, Any]] = None) -> str:
    """
    Собирает полный URL: base + path и query-string из params.

    Параметры ``params`` передаются в ``urllib.parse.urlencode``; значения приводятся
    к строке (числа и bool допустимы). Ключи вроде ``filters[branch_id]`` кодируются
    как ожидает бэкенд (скобки не экранируются лишний раз — urlencode это обрабатывает).

    :param base: Схема + хост без завершающего слэша, например PUBLIC_API.
    :param path: Путь, обычно начинается с ``/``, например ``/stores``.
    :param params: Словарь query-параметров или None.
    :return: Готовая строка URL.
    """
    path = path if path.startswith("/") else f"/{path}"
    url = base.rstrip("/") + path
    if params:
        # doseq=True: значения-списки дадут повторяющиеся ключи (если понадобится).
        q = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None},
            doseq=True,
        )
        if q:
            url = f"{url}?{q}"
    return url


def _http_get_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    headers: Optional[MutableMapping[str, str]] = None,
) -> Any:
    """
    Выполняет GET-запрос и разбирает тело как JSON.

    Используется стандартная библиотека (без requests), чтобы скрипт работал «из коробки».

    :param url: Полный URL после :func:`_build_url`.
    :param timeout: Таймаут сокета в секундах.
    :param headers: Дополнительные заголовки; по умолчанию задаётся User-Agent и Accept.
    :return: Распарсенный JSON (обычно ``list`` или ``dict``).
    :raises urllib.error.HTTPError: При кодах 4xx/5xx (тело ошибки доступно в исключении).
    :raises json.JSONDecodeError: Если ответ не JSON.
    """
    h = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if headers:
        h.update(headers)

    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, method="GET", headers=dict(h))

    url_short = url if len(url) <= 160 else url[:157] + "..."
    logger.info("ttexchange http GET start %s timeout=%.1fs", url_short, timeout)
    t0 = time.perf_counter()
    with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        raw = resp.read().decode(charset, errors="replace")
    logger.info(
        "ttexchange http GET done in %.2fs (%d chars) %s",
        time.perf_counter() - t0,
        len(raw),
        url_short,
    )
    return json.loads(raw)


def unwrap_value(payload: Any) -> Any:
    """
    Нормализует ответы в стиле OData/Microsoft: ``{"value": [...]}`` → сам список.

    Некоторые эндпоинты (например ``/stores``) в инструментах отображаются как объект
    с полем ``value``; другие сразу возвращают массив. Эта функция унифицирует доступ.

    :param payload: Сырой ответ :func:`_http_get_json`.
    :return: Если есть ключ ``value`` и он не ``None`` — возвращает ``payload["value"]``,
             иначе возвращает ``payload`` без изменений.
    """
    if isinstance(payload, dict) and "value" in payload:
        return payload["value"]
    return payload


def get_json(
    base: str,
    path: str,
    params: Optional[Mapping[str, Any]] = None,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    unwrap: bool = False,
) -> Any:
    """
    Удобная обёртка: построить URL, GET, опционально снять оболочку ``value``.

    :param base: PUBLIC_API или RATES_API.
    :param path: Путь API.
    :param params: Query-параметры.
    :param timeout: Таймаут запроса.
    :param unwrap: Передать True, если ожидается обёртка ``{"value": ...}``.
    :return: Данные JSON (тип зависит от эндпоинта).
    """
    url = _build_url(base, path, params)
    data = _http_get_json(url, timeout=timeout)
    return unwrap_value(data) if unwrap else data


# =============================================================================
# PUBLIC_API — филиалы, контент, «Паттайя», сейфы, отзывы
# Во всех методах ниже параметр ``lang`` — код языка интерфейса сайта (en, th, ru, …).
# =============================================================================


def get_store_groups(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Список групп филиалов (районы/кластеры), к которым привязаны точки обмена.

    Запрос: ``GET /store_groups?lang=...``

    Используется на сайте для фильтрации списка обменников по группе.
    """
    return get_json(PUBLIC_API, "/store_groups", {"lang": lang}, timeout=timeout, unwrap=True)


def get_stores(
    lang: str = DEFAULT_LANG,
    *,
    store_group_id: Optional[int] = None,
    is_hq: Optional[bool] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Список обменных пунктов (филиалов) или один «головной» офис.

    Запрос: ``GET /stores`` с параметрами:

    * ``lang`` — язык подписей;
    * ``store_group_id`` — если задан, только филиалы этой группы;
    * ``is_hq`` — если True, в запрос уходит ``is_hq=true`` (как на фронте) для выбора HQ.

    Поле ``branch_id`` в элементах списка нужно для API курсов (см. :func:`get_currencies`).
    """
    params: Dict[str, Any] = {"lang": lang}
    if store_group_id is not None:
        params["store_group_id"] = store_group_id
    if is_hq is True:
        params["is_hq"] = "true"
    return get_json(PUBLIC_API, "/stores", params, timeout=timeout, unwrap=True)


def get_banners(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Баннеры для главной и промо-блоков.

    ``GET /banners?lang=...``
    """
    return get_json(PUBLIC_API, "/banners", {"lang": lang}, timeout=timeout, unwrap=True)


def get_promotions(
    lang: str = DEFAULT_LANG,
    *,
    current_promotion_id: Optional[int] = None,
    order: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Список акций / промо-материалов.

    ``GET /promotions`` с параметрами:

    * ``current_promotion_id`` — если указан, фронт передаёт его как фильтр (см. исходники SPA);
    * ``order``, ``limit``, ``offset`` — сортировка и пагинация на стороне API.
    """
    params: Dict[str, Any] = {"lang": lang}
    if current_promotion_id is not None:
        params["current_promotion_id"] = current_promotion_id
    if order is not None:
        params["order"] = order
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    return get_json(PUBLIC_API, "/promotions", params, timeout=timeout, unwrap=True)


def get_promotion_detail(
    promotion_id: Union[int, str],
    lang: str = DEFAULT_LANG,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Детальная карточка одной акции.

    ``GET /promotions/{id}?lang=...``
    """
    return get_json(
        PUBLIC_API,
        f"/promotions/{promotion_id}",
        {"lang": lang},
        timeout=timeout,
        unwrap=True,
    )


def get_news(
    lang: str = DEFAULT_LANG,
    *,
    current_news_id: Optional[int] = None,
    order: Optional[str] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Лента новостей.

    ``GET /news`` — параметры аналогичны акциям: ``current_news_id``, ``order``, ``limit``, ``offset``.
    """
    params: Dict[str, Any] = {"lang": lang}
    if current_news_id is not None:
        params["current_news_id"] = current_news_id
    if order is not None:
        params["order"] = order
    if limit is not None:
        params["limit"] = limit
    if offset is not None:
        params["offset"] = offset
    return get_json(PUBLIC_API, "/news", params, timeout=timeout, unwrap=True)


def get_news_detail(
    news_id: Union[int, str],
    lang: str = DEFAULT_LANG,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Одна новость: ``GET /news/{id}?lang=...``."""
    return get_json(
        PUBLIC_API,
        f"/news/{news_id}",
        {"lang": lang},
        timeout=timeout,
        unwrap=True,
    )


def get_abouts(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Тексты раздела «О нас» (контентные блоки).

    ``GET /abouts?lang=...``
    """
    return get_json(PUBLIC_API, "/abouts", {"lang": lang}, timeout=timeout, unwrap=True)


def get_about_facts(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Факты/цифры для страницы «О нас» (отдельный список от :func:`get_abouts`).

    ``GET /about_facts?lang=...``
    """
    return get_json(PUBLIC_API, "/about_facts", {"lang": lang}, timeout=timeout, unwrap=True)


def get_landing_pages(
    lang: str = DEFAULT_LANG,
    *,
    place_type_id: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Лендинги раздела «Исследуйте Паттайю» (привязка к типам мест).

    ``GET /landing_pages/?lang=...`` — на фронте путь с завершающим слэшем.

    При необходимости передаётся ``place_type_id``.
    """
    params: Dict[str, Any] = {"lang": lang}
    if place_type_id is not None:
        params["place_type_id"] = place_type_id
    return get_json(PUBLIC_API, "/landing_pages/", params, timeout=timeout, unwrap=True)


def get_place_types(
    lang: str = DEFAULT_LANG,
    *,
    with_place: Union[bool, str, int, None] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Типы мест (категории для гида по Паттайе).

    ``GET /place_types?lang=...&with_place=...``

    Параметр ``with_place`` на фронте пробрасывается как есть (часто строка или флаг).
    """
    params: Dict[str, Any] = {"lang": lang}
    if with_place is not None:
        params["with_place"] = with_place
    return get_json(PUBLIC_API, "/place_types", params, timeout=timeout, unwrap=True)


def get_places(
    lang: str = DEFAULT_LANG,
    *,
    place_type_id: Optional[int] = None,
    current_place_id: Optional[int] = None,
    order: Optional[str] = None,
    limit: Optional[int] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Список мест (достопримечательности, заведения и т.д.).

    ``GET /places`` с параметрами:

    * ``place_type_id`` — фильтр по типу;
    * ``current_place_id`` — выделение/контекст одного места (как в SPA);
    * ``order``, ``limit`` — сортировка и ограничение выборки.
    """
    params: Dict[str, Any] = {"lang": lang}
    if place_type_id is not None:
        params["place_type_id"] = place_type_id
    if current_place_id is not None:
        params["current_place_id"] = current_place_id
    if order is not None:
        params["order"] = order
    if limit is not None:
        params["limit"] = limit
    return get_json(PUBLIC_API, "/places", params, timeout=timeout, unwrap=True)


def get_place_detail(
    place_id: Union[int, str],
    lang: str = DEFAULT_LANG,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Карточка места: ``GET /places/{id}?lang=...``."""
    return get_json(
        PUBLIC_API,
        f"/places/{place_id}",
        {"lang": lang},
        timeout=timeout,
        unwrap=True,
    )


def get_faqs(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Вопросы и ответы (FAQ).

    ``GET /faqs?lang=...``
    """
    return get_json(PUBLIC_API, "/faqs", {"lang": lang}, timeout=timeout, unwrap=True)


def get_safe_box_branches(lang: str = DEFAULT_LANG, *, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Филиалы, где доступны услуги сейфов (safe deposit).

    ``GET /safe_box_branches?lang=...``
    """
    return get_json(PUBLIC_API, "/safe_box_branches", {"lang": lang}, timeout=timeout, unwrap=True)


def get_safe_box_branch_detail(
    branch_id: Union[int, str],
    lang: str = DEFAULT_LANG,
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Подробности по одному филиалу сейфов.

    ``GET /safe_box_branches/{id}?lang=...``
    """
    return get_json(
        PUBLIC_API,
        f"/safe_box_branches/{branch_id}",
        {"lang": lang},
        timeout=timeout,
        unwrap=True,
    )


def get_testimonials(*, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """
    Отзывы клиентов.

    ``GET /testimonials`` — без query-параметров (так реализовано в Angular-сервисе).
    """
    return get_json(PUBLIC_API, "/testimonials", None, timeout=timeout, unwrap=True)


# =============================================================================
# RATES_API — курсы валют
# =============================================================================


def get_currencies(
    branch_id: Union[int, str],
    *,
    is_main: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Актуальные курсы валют для выбранного филиала.

    Запрос: ``GET /currencies?is_main=...&branch_id=...``

    На сайте для витрины курсов передаётся ``is_main=false`` (строка ``"false"`` в query).
    Если передать ``is_main=True``, уйдёт ``is_main=true`` — поведение зависит от бэкенда.

    :param branch_id: Идентификатор филиала из объекта магазина (поле ``branch_id`` в :func:`get_stores`).
    """
    params = {"is_main": "true" if is_main else "false", "branch_id": str(branch_id)}
    return get_json(RATES_API, "/currencies", params, timeout=timeout, unwrap=True)


def get_currency_histories(
    *,
    branch_id: Optional[Union[int, str]] = None,
    currency_id: Optional[Union[int, str]] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    История курсов (как формирует фронт).

    ``GET /currencies/histories`` с фильтрами в виде отдельных query-ключей:

    * ``filters[branch_id]``
    * ``filters[currency_id]``
    * ``filters[start_date]``
    * ``filters[end_date]``

    Даты — в том формате, который ожидает API (часто ``YYYY-MM-DD``); уточняйте по ответу/ошибкам.
    """
    params: Dict[str, Any] = {}
    if branch_id is not None:
        params["filters[branch_id]"] = branch_id
    if currency_id is not None:
        params["filters[currency_id]"] = currency_id
    if start_date is not None:
        params["filters[start_date]"] = start_date
    if end_date is not None:
        params["filters[end_date]"] = end_date
    return get_json(RATES_API, "/currencies/histories", params, timeout=timeout, unwrap=True)


def get_currencies_updated_at(
    branch_id: Union[int, str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """
    Время последнего обновления котировок для филиала.

    ``GET /currencies/updated_at?branch_id=...``
    """
    return get_json(
        RATES_API,
        "/currencies/updated_at",
        {"branch_id": str(branch_id)},
        timeout=timeout,
        unwrap=True,
    )


# ---------------------------------------------------------------------------
# Вспомогательные сценарии для CLI
# ---------------------------------------------------------------------------


def _pick_default_branch_id(stores: Any) -> Optional[str]:
    """
    Из списка магазинов выбирает ``branch_id``: сначала филиал с ``is_hq``, иначе первый.

    :param stores: Результат :func:`get_stores` (ожидается итерируемый список словарей).
    :return: Строковый ``branch_id`` или None, если список пуст/без полей.
    """
    if not isinstance(stores, list):
        return None
    for row in stores:
        if isinstance(row, dict) and row.get("is_hq") and row.get("branch_id") is not None:
            return str(row["branch_id"])
    for row in stores:
        if isinstance(row, dict) and row.get("branch_id") is not None:
            return str(row["branch_id"])
    return None


def _print_rates_table(rows: Any) -> None:
    """Печатает таблицу курсов в stdout (для демонстрации в CLI)."""
    if not isinstance(rows, list) or not rows:
        print("Нет данных по курсам.")
        return
    w = max(len(str(r.get("name", ""))) for r in rows)
    w = max(w, len("Валюта"))
    print(f"{'Валюта'.ljust(w)}  Покупка  Продажа  Примечание")
    print("-" * (w + 28))
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = str(r.get("name", "")).ljust(w)
        buy = r.get("current_buy_rate")
        sell = r.get("current_sell_rate")
        buy_s = str(buy) if buy is not None else "—"
        sell_s = str(sell) if sell is not None else "—"
        note = r.get("description") or ""
        print(f"{name}  {buy_s:>7}  {sell_s:>7}  {note}")


def cli_main(argv: Optional[List[str]] = None) -> int:
    """
    Точка входа для режима командной строки: демонстрация вызовов без отдельного кода.

    Подкоманды упрощены; для произвольных запросов импортируйте функции модуля из Python.
    """
    parser = argparse.ArgumentParser(description="Клиент публичных API ttexchange.com")
    parser.add_argument("--lang", default=DEFAULT_LANG, help="Код языка для PUBLIC_API")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stores = sub.add_parser("stores", help="Список обменных филиалов")
    p_stores.add_argument("--group", type=int, default=None, help="store_group_id")
    p_stores.add_argument("--hq", action="store_true", help="Только головной офис (is_hq=true)")

    sub.add_parser("store-groups", help="Группы филиалов")

    p_rates = sub.add_parser("rates", help="Курсы валют по branch_id")
    p_rates.add_argument("--branch", type=str, default=None, help="branch_id; если нет — авто из stores")

    sub.add_parser("banners", help="GET /banners")
    sub.add_parser("promotions", help="GET /promotions")
    sub.add_parser("news", help="GET /news")
    sub.add_parser("abouts", help="GET /abouts")
    sub.add_parser("about-facts", help="GET /about_facts")
    sub.add_parser("landing-pages", help="GET /landing_pages/")
    sub.add_parser("place-types", help="GET /place_types")
    sub.add_parser("places", help="GET /places")
    sub.add_parser("faqs", help="GET /faqs")
    sub.add_parser("safe-box-branches", help="GET /safe_box_branches")
    sub.add_parser("testimonials", help="GET /testimonials")

    args = parser.parse_args(argv)

    try:
        if args.cmd == "stores":
            data = get_stores(
                args.lang,
                store_group_id=args.group,
                is_hq=True if args.hq else None,
            )
        elif args.cmd == "store-groups":
            data = get_store_groups(args.lang)
        elif args.cmd == "rates":
            branch = args.branch
            if not branch:
                stores = get_stores(args.lang)
                branch = _pick_default_branch_id(stores)
                if not branch:
                    print("Не удалось определить branch_id. Задайте --branch.", file=sys.stderr)
                    return 1
            data = get_currencies(branch, is_main=False)
            _print_rates_table(data)
            return 0
        elif args.cmd == "banners":
            data = get_banners(args.lang)
        elif args.cmd == "promotions":
            data = get_promotions(args.lang)
        elif args.cmd == "news":
            data = get_news(args.lang)
        elif args.cmd == "abouts":
            data = get_abouts(args.lang)
        elif args.cmd == "about-facts":
            data = get_about_facts(args.lang)
        elif args.cmd == "landing-pages":
            data = get_landing_pages(args.lang)
        elif args.cmd == "place-types":
            data = get_place_types(args.lang)
        elif args.cmd == "places":
            data = get_places(args.lang)
        elif args.cmd == "faqs":
            data = get_faqs(args.lang)
        elif args.cmd == "safe-box-branches":
            data = get_safe_box_branches(args.lang)
        elif args.cmd == "testimonials":
            data = get_testimonials()
        else:
            parser.error(f"Неизвестная команда: {args.cmd}")
            return 2

        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        print(f"HTTP {e.code}: {e.reason}\n{body[:800]}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Сеть: {e.reason}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as e:
        print(f"Ответ не JSON: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli_main())
