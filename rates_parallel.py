#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Параллельное выполнение блокирующих вызовов (план A: ThreadPoolExecutor).

Используется в ``exchange_report``, ``cash_report``, ``usdt_report``,
:func:`rates_sources.run_sources`. При переходе на async (план B) замените
тело :func:`map_bounded`, сохранив сигнатуру и порядок результатов.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Callable, List, Optional, Sequence, Tuple, TypeVar

T = TypeVar("T")
R = TypeVar("R")

MapOutcome = Tuple[T, Optional[R], Optional[Exception]]


def default_max_workers() -> int:
    raw = (os.environ.get("RATES_PARALLEL_MAX_WORKERS") or "").strip()
    if raw:
        try:
            n = int(raw)
            return max(1, min(n, 256))
        except ValueError:
            pass
    return 12


def map_bounded(
    items: Sequence[T],
    func: Callable[[T], R],
    *,
    max_workers: Optional[int] = None,
) -> List[MapOutcome]:
    """
    Для каждого ``item`` вызывает ``func(item)`` в ограниченном пуле потоков.

    Все задачи ставятся в очередь сразу; одновременно работает не больше
    ``max_workers`` (или ``RATES_PARALLEL_MAX_WORKERS`` / 12). Итоговое время
    близко к **максимуму** длительностей задач, а не к сумме — даже если в логах
    «done» идут один за другим (у каждого потока свой конец).

    Порядок элементов в списке совпадает с ``items``. Исключения из ``func``
    не пробрасываются: они приходят третьим полем кортежа (``Exception``).

    Примечание: ``ThreadPoolExecutor.map`` на главном потоке вызывает
    ``Future.result()`` **по порядку** списка, из‑за чего кажется, что «ждём
    первый, потом второй»; сами воркеры при этом уже крутятся параллельно.
    Здесь после ``wait`` берём ``.result()`` в том же порядке ``items``, когда
    **все** задачи завершены — поведение для вызывающего то же, семантика
    параллелизма явная.
    """
    cap = default_max_workers() if max_workers is None else max(1, max_workers)
    if not items:
        return []

    def one(it: T) -> MapOutcome:
        try:
            return (it, func(it), None)
        except Exception as exc:
            return (it, None, exc)

    workers = min(cap, len(items))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(one, it) for it in items]
        wait(futures)
        return [f.result() for f in futures]
