# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import importlib

from rates_sources import FetchContext, SourceCategory

mob = importlib.import_module("sources.kwikpay.kwikpay_mob")

_ACCOUNT_RESP = {
    "fees": [
        {
            "acceptedTransferAmount": 50000.0,
            "withdrawAmount": 22294.21,
            "withdrawCurrency": "THB",
            "rate": 0.4458864774,
            "acceptedTotalFee": 0.0,
        }
    ]
}

_CARD_RESP = {
    "fees": [
        {
            "acceptedTransferAmount": 36526.65,
            "withdrawAmount": 500.0,
            "withdrawCurrency": "USD",
            "rate": 0.0136886355578735,
            "acceptedTotalFee": 438.32,
        }
    ]
}


class TestKwikpayMob(unittest.TestCase):
    def setUp(self) -> None:
        self._env = mock.patch.dict(
            os.environ,
            {
                "KWIKPAY_AUTH_TOKEN": "test-token",
                "KWIKPAY_SENDER_BANK_ID": "9000598",
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    @mock.patch("sources.kwikpay.kwikpay_mob.urlopen_retriable")
    def test_post_commissions_auth_error(self, urlopen_mock) -> None:
        import urllib.error

        body = (
            b'{"error":{"type":"Errors::Unauthenticated","message":"'
            b"\xd0\x92\xd0\xb0\xd1\x88 \xd0\xbd\xd0\xbe\xd0\xbc\xd0\xb5\xd1\x80 \xd1\x82\xd0\xb5\xd0\xbb\xd0\xb5\xd1\x84\xd0\xbe\xd0\xbd\xd0\xb0"
            b' \xd0\xbd\xd0\xb5 \xd0\xbf\xd0\xbe\xd0\xb4\xd1\x82\xd0\xb2\xd0\xb5\xd1\x80\xd0\xb6\xd0\xb4\xd0\xb5\xd0\xbd"}}'
        )

        class _Err(urllib.error.HTTPError):
            def read(self):
                return body

        urlopen_mock.side_effect = _Err(
            mob._COMMISSIONS_URL, 401, "Unauthorized", hdrs=None, fp=None
        )
        with self.assertRaises(mob.KwikpayAuthError) as ctx:
            mob.fetch_overseas_deposits_thb(50_000)
        self.assertIn("KWIKPAY_REFRESH_TOKEN", str(ctx.exception))
        self.assertIn("номер телефона", str(ctx.exception))

    @mock.patch("sources.kwikpay.kwikpay_mob.patch_repo_dotenv", return_value=True)
    @mock.patch("sources.kwikpay.kwikpay_mob.urlopen_retriable")
    def test_post_commissions_refreshes_session_on_401(
        self, urlopen_mock, patch_dotenv_mock
    ) -> None:
        import urllib.error

        os.environ["KWIKPAY_REFRESH_TOKEN"] = "old-refresh"
        self.addCleanup(os.environ.pop, "KWIKPAY_REFRESH_TOKEN", None)

        auth_body = (
            b'{"error":{"type":"Errors::Unauthenticated","message":"expired"}}'
        )
        refresh_body = json.dumps(
            {
                "access_token": "new-access",
                "refresh_token": "new-refresh",
                "technical_work": None,
                "need_document_auth": False,
            }
        ).encode()
        ok_body = json.dumps(_ACCOUNT_RESP).encode()

        class _Resp:
            def __init__(self, raw: bytes):
                self._raw = raw

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _Err(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    mob._COMMISSIONS_URL, 401, "Unauthorized", hdrs=None, fp=None
                )

            def read(self):
                return auth_body

        calls: list[str] = []

        def _side_effect(req, **kwargs):
            url = req.full_url
            calls.append(url)
            if url == mob._REFRESH_SESSION_URL:
                return _Resp(refresh_body)
            if url == mob._COMMISSIONS_URL:
                if calls.count(mob._COMMISSIONS_URL) == 1:
                    raise _Err()
                return _Resp(ok_body)
            raise AssertionError(url)

        urlopen_mock.side_effect = _side_effect
        fee = mob.fetch_overseas_deposits_thb(50_000)
        self.assertAlmostEqual(fee.rub_per_thb, 50000 / 22294.21, places=3)
        self.assertEqual(os.environ["KWIKPAY_AUTH_TOKEN"], "new-access")
        self.assertEqual(os.environ["KWIKPAY_REFRESH_TOKEN"], "new-refresh")
        patch_dotenv_mock.assert_called_once()
        self.assertEqual(
            patch_dotenv_mock.call_args[0][1]["KWIKPAY_AUTH_TOKEN"],
            "new-access",
        )

    @mock.patch("sources.kwikpay.kwikpay_mob.urlopen_retriable")
    def test_post_commissions_headers(self, urlopen_mock) -> None:
        class _Resp:
            def read(self):
                return json.dumps(_ACCOUNT_RESP).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        urlopen_mock.return_value = _Resp()
        fee = mob.fetch_overseas_deposits_thb(50_000)
        self.assertAlmostEqual(fee.rub_per_thb, 50000 / 22294.21, places=3)
        req = urlopen_mock.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        hdr = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(hdr.get("x-auth-token"), "test-token")
        self.assertEqual(hdr.get("x-app-version"), "3.31.0")
        self.assertEqual(hdr.get("user-agent"), "okhttp/4.12.0")

    @mock.patch(
        "sources.unired_bkb.bbl_latest_fx.subscription_key_from_env",
        return_value="test-bbl-key",
    )
    @mock.patch("sources.unired_bkb.bbl_latest_fx.fetch_usd50_tt_thb", return_value=35.0)
    @mock.patch("sources.kwikpay.kwikpay_mob.post_commissions")
    def test_summary_card_rub_per_thb_via_bbl(self, post_mock, bbl_mock, _key_mock) -> None:
        post_mock.side_effect = [_ACCOUNT_RESP, _CARD_RESP]
        kw = importlib.import_module("sources.kwikpay")
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        quotes = kw.summary(ctx)
        self.assertEqual(len(quotes), 2)
        self.assertEqual(quotes[0].category, SourceCategory.TRANSFER)
        self.assertEqual(quotes[0].label, "KwikPay счёт")
        self.assertEqual(quotes[1].label, "KwikPay карта")
        rub_per_usd = 36526.65 / 500.0
        self.assertAlmostEqual(quotes[1].rate, rub_per_usd / 35.0, places=3)
        self.assertTrue(quotes[1].compare_to_baseline)
        bbl_mock.assert_called_once()

    @mock.patch("sources.kwikpay.kwikpay_mob.fetch_visa_direct_usd")
    @mock.patch("sources.kwikpay.kwikpay_mob.fetch_overseas_deposits_thb")
    def test_receiving_thb_scales_card_usd(self, acc_mock, card_mock) -> None:
        acc_mock.return_value = mob.KwikpayMobFee(
            "OverseasDeposits", 50_000, 22_000, "THB", 0, 0
        )

        def _card(usd: float, **_kw) -> mob.KwikpayMobFee:
            return mob.KwikpayMobFee(
                "VisaDirect", usd * 73.0, usd, "USD", 100.0, 0
            )

        card_mock.side_effect = _card
        fees = mob.fetch_summary_fees(receiving_thb=30_000, thb_per_usd=35.0)
        self.assertEqual(len(fees), 2)
        card = fees[1]
        self.assertAlmostEqual(card.withdraw_amount * 35.0, 30_000, delta=1.0)
        self.assertGreaterEqual(card_mock.call_count, 2)

    @mock.patch(
        "sources.unired_bkb.bbl_latest_fx.subscription_key_from_env",
        return_value="test-bbl-key",
    )
    @mock.patch("sources.unired_bkb.bbl_latest_fx.fetch_usd50_tt_thb", return_value=35.0)
    @mock.patch("sources.kwikpay.kwikpay_mob.fetch_summary_fees")
    def test_summary_receiving_thb_passes_bbl_to_fees(
        self, fees_mock, _bbl_mock, _key_mock
    ) -> None:
        fees_mock.return_value = [
            mob.KwikpayMobFee("OverseasDeposits", 1, 1, "THB", 0, 0),
            mob.KwikpayMobFee("VisaDirect", 1, 1, "USD", 0, 0),
        ]
        kw = importlib.import_module("sources.kwikpay")
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None, receiving_thb=30_000)
        quotes = kw.summary(ctx)
        self.assertEqual(len(quotes), 2)
        self.assertIn("30 000", quotes[1].note)
        fees_mock.assert_called_once()
        _args, kwargs = fees_mock.call_args
        self.assertEqual(kwargs.get("receiving_thb"), 30_000.0)
        self.assertEqual(kwargs.get("thb_per_usd"), 35.0)


if __name__ == "__main__":
    unittest.main()
