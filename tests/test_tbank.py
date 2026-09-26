# -*- coding: utf-8 -*-
from __future__ import annotations

import ssl
import unittest
import urllib.error
from unittest import mock

import rates_sources  # noqa: F401  — полный импорт плагинов до sources.tbank
from sources import tbank as tbank_mod


class _JsonResp:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw
        self.headers = mock.Mock()
        self.headers.get_content_charset.return_value = "utf-8"

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_JsonResp":
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class TestTbankSslFallback(unittest.TestCase):
    def test_load_rates_retries_unverified_after_cert_error(self) -> None:
        payload = b'{"resultCode":"OK","payload":{"rates":[]}}'
        contexts = []

        def fake_urlopen(req, timeout=None, context=None, **kwargs):
            contexts.append(context)
            if context is None or context.verify_mode != ssl.CERT_NONE:
                raise urllib.error.URLError(
                    ssl.SSLError("self-signed certificate in certificate chain")
                )
            return _JsonResp(payload)

        with mock.patch.object(tbank_mod, "urlopen_retriable", side_effect=fake_urlopen):
            data = tbank_mod._load_rates_json(timeout=1.0)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data.get("resultCode"), "OK")
        self.assertEqual(len(contexts), 2)
        self.assertEqual(contexts[1].verify_mode, ssl.CERT_NONE)
