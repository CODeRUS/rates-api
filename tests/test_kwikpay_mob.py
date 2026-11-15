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

    @mock.patch("sources.kwikpay.kwikpay_rates.fetch_summary_quote")
    def test_summary_uses_site_account_quote(self, quote_mock) -> None:
        rates = importlib.import_module("sources.kwikpay.kwikpay_rates")
        quote_mock.return_value = rates.KwikQuote(
            amount_input=50_000,
            withdraw_thb=19_805.98,
            pay_rub=50_000,
            fee_rub=0,
            rub_per_thb=50_000 / 19_805.98,
            api_rate=0.3961165932,
        )
        kw = importlib.import_module("sources.kwikpay")
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        quotes = kw.summary(ctx)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].category, SourceCategory.TRANSFER)
        self.assertEqual(quotes[0].label, "KwikPay счёт")
        self.assertAlmostEqual(quotes[0].rate, 50_000 / 19_805.98, places=6)
        self.assertIn("50 000", quotes[0].note)
        self.assertTrue(quotes[0].compare_to_baseline)
        quote_mock.assert_called_once_with(receiving_thb=None)

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
        self.assertLessEqual(card.withdraw_amount, mob._VISA_DIRECT_USD_MAX)

    @mock.patch("sources.kwikpay.kwikpay_mob.fetch_visa_direct_usd")
    @mock.patch("sources.kwikpay.kwikpay_mob.fetch_overseas_deposits_thb")
    def test_receiving_thb_clamps_card_usd_to_api_max(self, acc_mock, card_mock) -> None:
        acc_mock.return_value = mob.KwikpayMobFee(
            "OverseasDeposits", 50_000, 22_000, "THB", 0, 0
        )
        seen: list[float] = []

        def _card(usd: float, **_kw) -> mob.KwikpayMobFee:
            seen.append(usd)
            self.assertLessEqual(usd, mob._VISA_DIRECT_USD_MAX)
            return mob.KwikpayMobFee("VisaDirect", usd * 73.0, usd, "USD", 100.0, 0)

        card_mock.side_effect = _card
        # 100_000 THB / 35 ≈ 2857 USD > API max 1000 → clamp
        fees = mob.fetch_summary_fees(receiving_thb=100_000, thb_per_usd=35.0)
        self.assertEqual(len(fees), 2)
        self.assertEqual(fees[1].withdraw_amount, mob._VISA_DIRECT_USD_MAX)
        self.assertTrue(all(u <= mob._VISA_DIRECT_USD_MAX for u in seen))

    @mock.patch("sources.kwikpay.kwikpay_rates.fetch_summary_quote")
    def test_summary_receiving_thb_uses_site_quote(self, quote_mock) -> None:
        rates = importlib.import_module("sources.kwikpay.kwikpay_rates")
        quote_mock.return_value = rates.KwikQuote(
            amount_input=75_684,
            withdraw_thb=30_000,
            pay_rub=75_684,
            fee_rub=0,
            rub_per_thb=75_684 / 30_000,
            api_rate=0.39,
        )
        kw = importlib.import_module("sources.kwikpay")
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None, receiving_thb=30_000)
        quotes = kw.summary(ctx)
        self.assertEqual(len(quotes), 1)
        self.assertEqual(quotes[0].label, "KwikPay счёт")
        self.assertIn("30 000", quotes[0].note)
        quote_mock.assert_called_once_with(receiving_thb=30_000.0)


class TestKwikpaySiteCalculator(unittest.TestCase):
    def _post(self, updates: list, *, fail_first_calculate: bool = False):
        calculates = {"n": 0}

        def post(_uri, _csrf, current, upd, calls, timeout=45.0):
            methods = [c.get("method") for c in calls]
            updates.append((dict(upd), methods))
            data = dict(current["data"])
            data.update(upd)
            memo: dict = {}
            amount = data.get("amount")
            if "calculate" in methods and amount not in (None, ""):
                calculates["n"] += 1
                principal = float(amount)
                if fail_first_calculate and calculates["n"] == 1:
                    data["fee"] = None
                    memo = {
                        "errors": {
                            "amount": [
                                "Ошибка расчёта. Попробуйте изменить сумму или валюту."
                            ]
                        }
                    }
                else:
                    fee = 0.0 if principal >= 50_000 else 250.0
                    data["fee"] = [
                        {
                            "withdrawAmount": (principal / 2.52449) if principal else 0,
                            "acceptedTransferAmount": principal + fee,
                            "acceptedTotalFee": fee,
                            "rate": 0.3961165932,
                        }
                    ]
            else:
                data["fee"] = None
            new = {"data": data, "memo": memo, "checksum": "a"}
            return {"components": [{"snapshot": json.dumps(new)}]}

        return post

    def _patches(self, rates, post):
        snap = {
            "data": {
                "fee": None,
                "country": None,
                "currency": None,
                "amount": None,
            },
            "memo": {},
            "checksum": "a",
        }
        return (
            mock.patch.object(rates, "_fetch", return_value="page"),
            mock.patch.object(
                rates, "_parse_livewire_config", return_value=("https://kwikpay.ru/x", "csrf")
            ),
            mock.patch.object(rates, "_parse_calculator_snapshot", return_value=snap),
            mock.patch.object(rates, "_livewire_post", side_effect=post),
        )

    def test_amount_step_does_not_resend_country(self) -> None:
        rates = importlib.import_module("sources.kwikpay.kwikpay_rates")
        updates: list = []
        patches = self._patches(rates, self._post(updates))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        quotes = rates.fetch_quotes_for_amounts([50_000, 1_000])
        self.assertEqual(updates[0], ({"country": "THA", "currency": "THB"}, ["calculate"]))
        self.assertEqual(updates[1], ({"amount": "50000"}, []))
        self.assertEqual(updates[2], ({}, ["calculate"]))
        self.assertEqual(updates[3], ({"amount": "1000"}, []))
        self.assertEqual(updates[4], ({}, ["calculate"]))
        self.assertAlmostEqual(quotes[0].pay_rub, 50_000)
        self.assertAlmostEqual(quotes[1].fee_rub, 250)
        self.assertAlmostEqual(quotes[1].pay_rub, 1_250)

    def test_summary_quote_scales_amount_for_target_thb(self) -> None:
        rates = importlib.import_module("sources.kwikpay.kwikpay_rates")
        updates: list = []
        patches = self._patches(rates, self._post(updates))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        quote = rates.fetch_summary_quote(receiving_thb=30_000)
        amounts = [upd.get("amount") for upd, _methods in updates if upd.get("amount")]
        self.assertEqual(amounts[0], "50000")
        scaled = float(amounts[1])
        self.assertGreater(scaled, 50_000)
        self.assertAlmostEqual(quote.amount_input, scaled)
        self.assertAlmostEqual(quote.withdraw_thb, scaled / 2.52449, delta=1.0)

    def test_calculate_retries_when_first_fee_is_empty(self) -> None:
        rates = importlib.import_module("sources.kwikpay.kwikpay_rates")
        updates: list = []
        patches = self._patches(rates, self._post(updates, fail_first_calculate=True))
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in patches])
        quote = rates.fetch_summary_quote()
        empty_calcs = [
            methods
            for upd, methods in updates
            if methods == ["calculate"] and not upd
        ]
        self.assertEqual(len(empty_calcs), 2)
        self.assertAlmostEqual(quote.pay_rub, 50_000)
        self.assertAlmostEqual(quote.withdraw_thb, 50_000 / 2.52449, delta=1.0)


if __name__ == "__main__":
    unittest.main()
