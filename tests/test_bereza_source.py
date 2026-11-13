# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

from rates_sources import FetchContext, SourceCategory
from sources.bereza import _browser_headers, _extract_to_amount, _http_error, _http_json, summary


def _ctx(*, receiving_thb: float | None = None) -> FetchContext:
    return FetchContext(
        thb_ref=2.5,
        atm_fee=220.0,
        korona_small_rub=100000.0,
        korona_large_thb=70000.0,
        avosend_rub=100000.0,
        unionpay_date=None,
        moex_override=None,
        receiving_thb=receiving_thb,
        warnings=[],
    )


class TestBerezaSource(unittest.TestCase):
    def test_browser_headers_include_saved_clearance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bereza_cf.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "cf_clearance": "clearance-token",
                        "user_agent": "Mozilla/5.0 Chrome/157.0.0.0 Safari/537.36",
                    },
                    fh,
                )
            with mock.patch.dict(os.environ, {"BEREZA_CF_FILE": path}):
                headers = _browser_headers()
        self.assertEqual(headers["Cookie"], "cf_clearance=clearance-token")
        self.assertEqual(headers["User-Agent"], "Mozilla/5.0 Chrome/157.0.0.0 Safari/537.36")
        self.assertIn('Chromium";v="157"', headers["sec-ch-ua"])
        self.assertEqual(headers["sec-fetch-site"], "same-origin")

    def test_http_json_skips_failed_proxy(self) -> None:
        import sources.bereza as bz

        class _Body:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"token":"ok"}'

            class headers:
                @staticmethod
                def get_content_charset():
                    return "utf-8"

        challenge = urllib.error.HTTPError(
            "https://bereza-exchange.com/api/access-token",
            403,
            "Forbidden",
            hdrs=None,
            fp=io.BytesIO(b"<html>Just a moment... _cf_chl_opt"),
        )
        bz._preferred_proxy = None
        with mock.patch.object(bz, "_ordered_proxy_urls", return_value=["http://a:1", "http://b:2"]):
            with mock.patch.object(bz, "urlopen_retriable", side_effect=[challenge, _Body()]) as opener:
                data = _http_json("https://bereza-exchange.com/api/access-token", headers={}, timeout=5)
        self.assertEqual(data["token"], "ok")
        self.assertEqual(opener.call_count, 2)
        self.assertIsNot(
            opener.call_args_list[0].kwargs["opener"],
            opener.call_args_list[1].kwargs["opener"],
        )
        self.assertEqual(bz._preferred_proxy, "http://b:2")

    def test_cloudflare_403_is_short(self) -> None:
        err = _http_error(403, "<html><title>Just a moment...</title><script>_cf_chl_opt")
        self.assertIn("Cloudflare", str(err))
        self.assertNotIn("<html>", str(err))
        self.assertLess(len(str(err)), 120)
    def test_extract_to_amount_candidates(self) -> None:
        self.assertEqual(_extract_to_amount({"to_amount": 123.4}), 123.4)
        self.assertEqual(_extract_to_amount({"result": "456.7"}), 456.7)
        self.assertEqual(_extract_to_amount({"data": {"converted_amount": 11}}), 11.0)

    @mock.patch("sources.bereza._http_json")
    def test_fetch_access_token_caches(self, m_http) -> None:
        import sources.bereza as bz

        bz._token_cache = ("", 0.0)
        m_http.return_value = {"token": "tok-1", "expires": time.time() + 600}
        t1 = bz.fetch_access_token()
        t2 = bz.fetch_access_token()
        self.assertEqual(t1, "tok-1")
        self.assertEqual(t2, "tok-1")
        self.assertEqual(m_http.call_count, 1)

    @mock.patch("sources.bereza.fetch_access_token", return_value="tok-x")
    @mock.patch("sources.bereza._http_json")
    def test_convert_sends_access_token_header(self, m_http, _tok) -> None:
        import sources.bereza as bz

        m_http.return_value = {
            "amount": 30000.0,
            "from_currency": "RUB (SBP)",
            "to_currency": "THB",
            "result": 12000.0,
            "rate": 0.4,
        }
        rate, thb = bz._convert_rub_to_thb_pair(30_000, "RUB (SBP)")
        self.assertAlmostEqual(thb, 12000.0)
        self.assertAlmostEqual(rate, 2.5)
        _args, kwargs = m_http.call_args
        self.assertIn("x-bereza-access-token", kwargs["headers"])
        self.assertEqual(kwargs["headers"]["x-bereza-access-token"], "tok-x")
        self.assertEqual(kwargs["headers"]["sec-fetch-site"], "same-origin")
        self.assertTrue(str(_args[0]).startswith(bz._CONVERT_URL))

    @mock.patch("sources.bereza._convert_rub_to_thb")
    def test_summary_returns_transfer_and_cash(self, m_convert) -> None:
        m_convert.side_effect = [2.51, 2.63]
        ctx = _ctx()
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].label, "Bereza СБП")
        self.assertEqual(rows[0].category, SourceCategory.EXCHANGER)
        self.assertEqual(rows[1].label, "Bereza Наличные")
        self.assertEqual(rows[1].category, SourceCategory.CASH_RUB)
        self.assertEqual(ctx.warnings, [])
        self.assertEqual(m_convert.call_count, 2)
        self.assertEqual(m_convert.call_args_list[0][0][0], 30_000.0)
        self.assertEqual(m_convert.call_args_list[1][0][0], 10_000.0)

    @mock.patch("sources.bereza._convert_rub_to_thb")
    @mock.patch("sources.bereza._convert_rub_to_thb_pair")
    def test_summary_scales_rub_when_receiving_thb_set(self, m_pair, m_convert) -> None:
        m_pair.return_value = (2.5, 10_000.0)
        m_convert.side_effect = [2.51, 2.63]
        ctx = _ctx(receiving_thb=20_000.0)
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        m_pair.assert_called_once()
        self.assertEqual(m_convert.call_count, 2)
        self.assertEqual(m_convert.call_args_list[0][0][0], 60_000.0)
        self.assertEqual(m_convert.call_args_list[1][0][0], 20_000.0)
        assert rows is not None
        self.assertEqual(ctx.warnings, [])


if __name__ == "__main__":
    unittest.main()
