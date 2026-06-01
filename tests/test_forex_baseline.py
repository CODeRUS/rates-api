# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import unittest
from unittest import mock


class TestForexBaseline(unittest.TestCase):
    def test_falls_back_to_er_when_xe_has_no_rub(self) -> None:
        baseline = importlib.import_module("sources.forex.baseline")
        with mock.patch.object(
            baseline.xe,
            "midmarket_convert",
            side_effect=KeyError("Нет курса в ответе для THB и/или RUB: 'RUB'"),
        ):
            with mock.patch.object(
                baseline,
                "_paid_xe_thb_rub",
                side_effect=RuntimeError("no creds"),
            ):
                with mock.patch.object(baseline.er, "convert", return_value=2.18) as er_conv:
                    rate, provider = baseline.thb_rub_rate()
        self.assertAlmostEqual(rate, 2.18)
        self.assertEqual(provider, "er_api")
        er_conv.assert_called_once_with(1.0, "THB", "RUB", timeout=30.0)

    def test_uses_xe_midmarket_when_available(self) -> None:
        baseline = importlib.import_module("sources.forex.baseline")
        with mock.patch.object(
            baseline.xe,
            "midmarket_convert",
            return_value={"result": 2.21},
        ):
            rate, provider = baseline.thb_rub_rate()
        self.assertAlmostEqual(rate, 2.21)
        self.assertEqual(provider, "xe_midmarket")


class TestKwikpayHttpError(unittest.TestCase):
    def test_auth_error_message_is_readable(self) -> None:
        mob = importlib.import_module("sources.kwikpay.kwikpay_mob")
        raw = (
            b'{"error":{"type":"Errors::Unauthenticated","message":"'
            b"\xd0\x92\xd0\xb0\xd1\x88 \xd0\xbd\xd0\xbe\xd0\xbc\xd0\xb5\xd1\x80 \xd1\x82\xd0\xb5\xd0\xbb\xd0\xb5\xd1\x84\xd0\xbe\xd0\xbd\xd0\xb0"
            b' \xd0\xbd\xd0\xb5 \xd0\xbf\xd0\xbe\xd0\xb4\xd1\x82\xd0\xb2\xd0\xb5\xd1\x80\xd0\xb6\xd0\xb4\xd0\xb5\xd0\xbd"}}'
        )
        detail = mob._format_http_error_detail(raw)
        self.assertIn("номер телефона", detail)
        self.assertNotIn("\\xd0", detail)


if __name__ == "__main__":
    unittest.main()
