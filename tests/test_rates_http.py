# -*- coding: utf-8 -*-
from __future__ import annotations

import ssl
import unittest
import urllib.error
import urllib.request
from unittest import mock

import rates_http


class TestRatesHttp(unittest.TestCase):
    def test_urlopen_retriable_retries_urlerror_then_ok(self) -> None:
        ctx = ssl.create_default_context()
        req = urllib.request.Request("https://example.com/test")
        ok = mock.MagicMock()
        ok.__enter__ = mock.Mock(return_value=ok)
        ok.__exit__ = mock.Mock(return_value=False)

        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[
                urllib.error.URLError("connection reset by peer"),
                urllib.error.URLError(OSError(104, "Connection reset by peer")),
                ok,
            ],
        ) as m:
            out = rates_http.urlopen_retriable(
                req,
                timeout=5.0,
                context=ctx,
                max_attempts_override=5,
                backoff_override=0.01,
            )
        self.assertIs(out, ok)
        self.assertEqual(m.call_count, 3)

    def test_urlopen_retriable_ssl_reason_no_retry_storm(self) -> None:
        ctx = ssl.create_default_context()
        req = urllib.request.Request("https://example.com/test")
        ssl_err = urllib.error.URLError(ssl.SSLError("certificate verify failed"))
        with mock.patch("urllib.request.urlopen", side_effect=ssl_err) as m:
            with self.assertRaises(urllib.error.URLError):
                rates_http.urlopen_retriable(
                    req,
                    timeout=5.0,
                    context=ctx,
                    max_attempts_override=4,
                    backoff_override=0.01,
                )
        self.assertEqual(m.call_count, 1)

    def test_is_retryable_http_status(self) -> None:
        self.assertTrue(
            isinstance(
                rates_http.RetryableHttpStatus(503),
                BaseException,
            )
        )
        self.assertTrue(rates_http.is_retryable_exception(rates_http.RetryableHttpStatus(502)))

    def test_call_retriable(self) -> None:
        n = {"i": 0}

        def flaky() -> str:
            n["i"] += 1
            if n["i"] < 2:
                raise ConnectionResetError(104, "reset")
            return "ok"

        out = rates_http.call_retriable(flaky, max_attempts_override=3, backoff_override=0.01)
        self.assertEqual(out, "ok")
        self.assertEqual(n["i"], 2)


if __name__ == "__main__":
    unittest.main()
