# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from sources.avosend_bkb import calc


class TestAvosendBkbCalc(unittest.TestCase):
    def test_parse_api_number_comma_decimal(self) -> None:
        self.assertAlmostEqual(calc.parse_api_number("32,52"), 32.52)

    def test_fee_and_convert_rate(self) -> None:
        fee, cr = calc.fee_and_convert_rate({"fee": "500", "convertRate": "0,01"})
        self.assertAlmostEqual(fee, 500.0)
        self.assertAlmostEqual(cr, 0.01)

    def test_rub_per_thb_chain(self) -> None:
        # rub=30000, fee=500, net=29500, cr=0.01 → usd=295, thb_per_usd=32.52 → thb=9593.4
        rub = 30000.0
        fee = 500.0
        cr = 0.01
        thb_per_usd = 32.52
        expected = rub / (295.0 * 32.52)
        self.assertAlmostEqual(calc.rub_per_thb(rub, fee, cr, thb_per_usd), expected)
        self.assertIsNone(calc.rub_per_thb(rub, rub + 1, cr, thb_per_usd))
        self.assertIsNone(calc.rub_per_thb(rub, fee, 0.0, thb_per_usd))


if __name__ == "__main__":
    unittest.main()
