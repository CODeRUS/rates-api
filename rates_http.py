#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Повторы HTTP-запросов при обрывах и временных сбоях (urllib и curl_cffi)."""

from __future__ import annotations

import errno
import logging
import os
import random
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BACKOFF_BASE = 0.5

# Повтор при этих кодах (после urlopen HTTPError — редко, т.к. обычно не кидается на 5xx одинаково везде).
RETRYABLE_HTTP_CODES: frozenset[int] = frozenset({408, 429, 502, 503, 504})


def _env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(0.01, float(raw))
    except ValueError:
        return default


def max_attempts() -> int:
    return _env_int("RATES_HTTP_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)


def backoff_base_sec() -> float:
    return _env_float("RATES_HTTP_BACKOFF_BASE", DEFAULT_BACKOFF_BASE)


def _sleep_backoff(attempt_index: int, *, base: float) -> None:
    delay = base * (2**attempt_index) + random.uniform(0, max(0.05, base * 0.25))
    time.sleep(delay)


def _urllib_reason_non_retryable(reason: object) -> bool:
    if isinstance(reason, ssl.SSLError):
        return True
    return False


def _is_retryable_urlerror(exc: urllib.error.URLError) -> bool:
    if _urllib_reason_non_retryable(exc.reason):
        return False
    return True


def _requests_retry_types() -> Tuple[Type[BaseException], ...]:
    try:
        import requests

        return (
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
            requests.exceptions.ConnectionError,
        )
    except ImportError:
        return ()


class RetryableHttpStatus(Exception):
    """Внутренний сигнал повторить запрос (ответ с временным HTTP-кодом)."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code} (retry)")


def is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, RetryableHttpStatus):
        return True
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_CODES
    if isinstance(exc, urllib.error.URLError):
        return _is_retryable_urlerror(exc)
    for t in _requests_retry_types():
        if isinstance(exc, t):
            return True
    try:
        from curl_cffi.curl import CurlError
    except ImportError:
        pass
    else:
        if isinstance(exc, CurlError):
            return True
    if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
        return True
    if isinstance(exc, OSError):
        if exc.errno in (
            errno.ECONNRESET,
            errno.ETIMEDOUT,
            errno.ECONNREFUSED,
            errno.EPIPE,
            errno.ECONNABORTED,
            104,  # ECONNRESET на Linux
        ):
            return True
    return False


def urlopen_retriable(
    req: urllib.request.Request,
    *,
    timeout: Optional[Any] = None,
    context: Optional[ssl.SSLContext] = None,
    max_attempts_override: Optional[int] = None,
    backoff_override: Optional[float] = None,
) -> Any:
    """
    Аналог :func:`urllib.request.urlopen` с повторами при
    :class:`~urllib.error.URLError`, разрыве соединения и временных HTTP-кодах.
    """
    attempts = max_attempts_override if max_attempts_override is not None else max_attempts()
    base = backoff_override if backoff_override is not None else backoff_base_sec()
    last: Optional[BaseException] = None

    def _open() -> Any:
        if context is not None and timeout is not None:
            return urllib.request.urlopen(req, timeout=timeout, context=context)
        if context is not None:
            return urllib.request.urlopen(req, context=context)
        if timeout is not None:
            return urllib.request.urlopen(req, timeout=timeout)
        return urllib.request.urlopen(req)

    for attempt in range(attempts):
        try:
            return _open()
        except urllib.error.HTTPError as e:
            last = e
            if attempt < attempts - 1 and e.code in RETRYABLE_HTTP_CODES:
                logger.debug(
                    "urlopen HTTP %s, retry %s/%s", e.code, attempt + 1, attempts
                )
                _sleep_backoff(attempt, base=base)
                continue
            raise
        except urllib.error.URLError as e:
            last = e
            if attempt < attempts - 1 and _is_retryable_urlerror(e):
                logger.debug("urlopen URLError %s, retry %s/%s", e, attempt + 1, attempts)
                _sleep_backoff(attempt, base=base)
                continue
            raise
    assert last is not None
    raise last


def call_retriable(
    fn: Callable[[], T],
    *,
    max_attempts_override: Optional[int] = None,
    backoff_override: Optional[float] = None,
) -> T:
    """Вызвать ``fn()`` с повторами при :func:`is_retryable_exception`."""
    attempts = max_attempts_override if max_attempts_override is not None else max_attempts()
    base = backoff_override if backoff_override is not None else backoff_base_sec()
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if attempt < attempts - 1 and is_retryable_exception(e):
                logger.debug("call_retriable %s, retry %s/%s", e, attempt + 1, attempts)
                _sleep_backoff(attempt, base=base)
                continue
            raise
    assert last is not None
    raise last
