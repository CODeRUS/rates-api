# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from bot.rshb_args import parse_rshb_command_args
from rates import parse_rshb_cli_args
from sources.rshb_unionpay import card_fx_calculator as cfx


class TestRshbCommandArgs(unittest.TestCase):
    def test_parse_rshb_cli_defaults(self) -> None:
        thb, fee = parse_rshb_cli_args([])
        self.assertEqual(thb, 30000.0)
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_cli_custom(self) -> None:
        thb, fee = parse_rshb_cli_args(["35000", "300"])
        self.assertEqual(thb, 35000.0)
        self.assertEqual(fee, 300.0)

    def test_parse_rshb_cli_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["-1", "250"])
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["30000", "0"])
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["30000", "250", "x"])

    def test_parse_rshb_bot_defaults(self) -> None:
        thb, fee = parse_rshb_command_args("/rshb")
        self.assertEqual(thb, 30000.0)
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_bot_custom(self) -> None:
        thb, fee = parse_rshb_command_args("/rshb 30000 250")
        self.assertEqual(thb, 30000.0)
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_bot_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 0 250")
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 30000 -1")
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 30000 250 1")


class TestRshbTextFormat(unittest.TestCase):
    @patch("sources.rshb_unionpay.card_fx_calculator._msk_now_str", return_value="27.03.2026, 15:15 (MSK)")
    @patch("sources.rshb_unionpay.card_fx_calculator.fetch_live_inputs")
    def test_build_rshb_text_contains_expected_blocks(self, m_fetch, _m_now) -> None:
        m_fetch.return_value = (
            0.2060,
            12.07,
            Decimal("12.74"),
            date(2026, 3, 27),
            Decimal("12.38"),
            date(2026, 3, 27),
            False,
            {},
        )
        txt = cfx.build_rshb_text(thb_net=30000.0, atm_fee_thb=250.0)
        self.assertIn("Курс THB/RUB:", txt)
        self.assertIn("💳 ОПЛАТА картами UnionPay", txt)
        self.assertIn("🏧 СНЯТИЕ 30 000.00 THB в банкомате", txt)
        self.assertIn("(с учётом комиссии банкомата 250.00 THB)", txt)
        self.assertIn("РСХБ CNY (РСХБ-брокер)", txt)
        self.assertIn("РСХБ CNY (РСХБ-приложение)", txt)
        self.assertIn("РСХБ RUB (2026-03-27)", txt)
        self.assertIn("*разница от биржевого курса MOEX CNY/RUB", txt)
        self.assertIn("27.03.2026, 15:15 (MSK)", txt)


if __name__ == "__main__":
    unittest.main()
