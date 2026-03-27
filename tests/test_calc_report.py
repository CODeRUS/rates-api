# -*- coding: utf-8 -*-
from __future__ import annotations

"""
Патчи вида ``sources.*`` требуют, чтобы граф импортов уже был согласован
(как при обычном ``rates.py``); иначе на части версий Python возможен
partially initialized module при первом обращении mock к ``sources``.
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

from bot.calc_args import parse_calc_command_args


def setUpModule() -> None:
    import rates  # noqa: F401 — прогрев загрузки источников до применения @patch


class TestCalcReport(unittest.TestCase):
    @patch("calc_report._askmoney_rub_thb_module")
    @patch("exchange_report.best_fiat_buy_thb_across_branches", return_value=(40.0, []))
    @patch("sources.rshb_unionpay.card_fx_calculator.fetch_live_inputs")
    def test_build_calc_tt_first_when_best(
        self,
        m_fli,
        m_tt,
        m_am,
    ) -> None:
        from calc_report import build_calc_report_text

        mm = MagicMock()
        mm.load_params.return_value = object()
        mm.rub_to_thb.return_value = 36_000
        m_am.return_value = mm
        m_fli.return_value = (
            0.19,
            12.0,
            Decimal("12.1"),
            date(2026, 3, 27),
            Decimal("12.2"),
            date(2026, 3, 27),
            False,
            {},
        )
        text, w = build_calc_report_text(
            budget_rub=100_000.0,
            fiat_code="usd",
            rub_per_fiat_unit=83.0,
            atm_fee_thb=250.0,
            refresh=True,
        )
        self.assertIsInstance(w, list)
        self.assertIn("TT Exchange USD", text)
        data_lines = [
            ln
            for ln in text.splitlines()
            if ln.strip()
            and ln[0].isdigit()
            and "TT Exchange USD" in ln
        ]
        self.assertTrue(data_lines, msg=text)
        top = data_lines[0]
        self.assertTrue(top.lstrip().startswith("1 "), msg=top)


class TestCalcArgs(unittest.TestCase):
    def test_parse_bot_message(self) -> None:
        b, f, r = parse_calc_command_args("/calc 100000 usd 83")
        self.assertEqual(b, 100_000.0)
        self.assertEqual(f, "usd")
        self.assertEqual(r, 83.0)


if __name__ == "__main__":
    unittest.main()
