# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

import rates_sources  # noqa: F401  — завершает загрузку плагинов до sources.multitransfer
from sources.multitransfer import _proxy_map, _request_commissions


class TestMultitransferProxyRequest(unittest.TestCase):
    def test_proxy_map(self) -> None:
        self.assertIsNone(_proxy_map(None))
        self.assertEqual(
            _proxy_map("http://10.0.0.8:3128"),
            {"http": "http://10.0.0.8:3128", "https": "http://10.0.0.8:3128"},
        )

    def test_commissions_post_uses_capture_proxy(self) -> None:
        calls = {}

        class _Resp:
            status_code = 200
            text = ""

            def json(self):
                return {"fees": []}

        def post(url, **kwargs):
            calls["url"] = url
            calls["kwargs"] = kwargs
            return _Resp()

        fake_requests = types.ModuleType("curl_cffi.requests")
        fake_requests.post = post
        fake_pkg = types.ModuleType("curl_cffi")
        fake_pkg.requests = fake_requests
        with mock.patch(
            "sources.multitransfer._load_capture_proxy",
            return_value="http://10.0.0.8:3128",
        ):
            with mock.patch.dict(
                sys.modules,
                {"curl_cffi": fake_pkg, "curl_cffi.requests": fake_requests},
            ):
                data = _request_commissions(1000)
        self.assertEqual(data, {"fees": []})
        self.assertEqual(
            calls["kwargs"]["proxies"],
            {"http": "http://10.0.0.8:3128", "https": "http://10.0.0.8:3128"},
        )
        self.assertNotIn("proxy", calls["kwargs"]["headers"])


if __name__ == "__main__":
    unittest.main()
