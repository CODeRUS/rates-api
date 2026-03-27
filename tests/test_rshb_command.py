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
        amounts, fee = parse_rshb_cli_args([])
        self.assertEqual(amounts, [30000.0])
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_cli_custom(self) -> None:
        amounts, fee = parse_rshb_cli_args(["35000", "300"])
        self.assertEqual(amounts, [35000.0])
        self.assertEqual(fee, 300.0)

    def test_parse_rshb_cli_multi(self) -> None:
        amounts, fee = parse_rshb_cli_args(
            ["30000", "20000", "10000", "5000", "1000", "250"]
        )
        self.assertEqual(amounts, [30000.0, 20000.0, 10000.0, 5000.0, 1000.0])
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_cli_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["-1", "250"])
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["30000", "0"])
        with self.assertRaises(ValueError):
            parse_rshb_cli_args(["30000", "250", "x"])

    def test_parse_rshb_bot_defaults(self) -> None:
        amounts, fee = parse_rshb_command_args("/rshb")
        self.assertEqual(amounts, [30000.0])
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_bot_custom(self) -> None:
        amounts, fee = parse_rshb_command_args("/rshb 30000 250")
        self.assertEqual(amounts, [30000.0])
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_bot_multi(self) -> None:
        amounts, fee = parse_rshb_command_args(
            "/rshb 30000 20000 1000 250"
        )
        self.assertEqual(amounts, [30000.0, 20000.0, 1000.0])
        self.assertEqual(fee, 250.0)

    def test_parse_rshb_bot_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 0 250")
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 30000 -1")
        with self.assertRaises(ValueError):
            parse_rshb_command_args("/rshb 30000 x")


class TestMaxThbForBudget(unittest.TestCase):
    def test_monotonic_and_fits_budget(self) -> None:
        budget = 80_000.0
        tmax = cfx.max_thb_net_for_atm_rub_budget(
            budget,
            cny_per_thb=0.19,
            atm_fee_thb=250.0,
            cny_rub=12.2,
            rub_card=False,
        )
        self.assertGreater(tmax, 0)
        cost = cfx.atm_rub_total_for_net(
            tmax,
            atm_fee_thb=250.0,
            cny_per_thb=0.19,
            cny_rub=12.2,
            rub_card=False,
        )
        self.assertLessEqual(cost, budget + 0.01)
        cost_over = cfx.atm_rub_total_for_net(
            tmax + 1.0,
            atm_fee_thb=250.0,
            cny_per_thb=0.19,
            cny_rub=12.2,
            rub_card=False,
        )
        self.assertGreater(cost_over, budget - 0.01)


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
        txt = cfx.build_rshb_text(thb_nets=(30000.0,), atm_fee_thb=250.0)
        self.assertIn("Курс THB/RUB:", txt)
        self.assertIn("💳 ОПЛАТА картами UnionPay", txt)
        self.assertIn("🏧 СНЯТИЕ 30 000.00 THB в банкомате", txt)
        self.assertIn("(с учётом комиссии банкомата 250.00 THB)", txt)
        self.assertIn("РСХБ CNY (РСХБ-брокер)", txt)
        self.assertIn("РСХБ CNY (РСХБ-приложение)", txt)
        self.assertIn("РСХБ RUB (2026-03-27)", txt)
        self.assertIn("*разница от биржевого курса MOEX CNY/RUB", txt)
        self.assertIn("27.03.2026, 15:15 (MSK)", txt)

    @patch("sources.rshb_unionpay.card_fx_calculator._msk_now_str", return_value="27.03.2026, 15:15 (MSK)")
    @patch("sources.rshb_unionpay.card_fx_calculator.fetch_live_inputs")
    def test_build_rshb_text_multiple_withdrawal_blocks(self, m_fetch, _m_now) -> None:
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
        txt = cfx.build_rshb_text(
            thb_nets=(30_000.0, 10_000.0, 1_000.0), atm_fee_thb=250.0
        )
        self.assertEqual(txt.count("💳 ОПЛАТА картами UnionPay"), 1)
        self.assertEqual(txt.count("🏧 СНЯТИЕ"), 3)
        self.assertIn("🏧 СНЯТИЕ 30 000.00 THB в банкомате", txt)
        self.assertIn("🏧 СНЯТИЕ 10 000.00 THB в банкомате", txt)
        self.assertIn("🏧 СНЯТИЕ 1 000.00 THB в банкомате", txt)
        self.assertEqual(txt.count("27.03.2026, 15:15 (MSK)"), 1)


if __name__ == "__main__":
    unittest.main()
