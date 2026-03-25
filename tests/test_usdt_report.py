# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import usdt_report


class TestUsdtReport(unittest.TestCase):
    def test_format_usdt_report_text(self):
        data = {
            "rub_per_usdt": {
                "bybit_cash": 100.0,
                "bybit_transfer": 99.0,
                "htx_cash": 101.0,
                "htx_no_cash": 98.5,
            },
            "thb_per_usdt": {
                "bitkub_highest_bid": 35.0,
                "binance_bid": 34.9,
            },
        }
        warnings = ["test warning"]
        text = usdt_report.format_usdt_report_text(data, warnings)
        self.assertIn("100 RUB/USDT", text)
        self.assertIn("35 THB/USDT", text)
        self.assertIn("Bybit P2P (cash) → Bitkub", text)
        self.assertIn("2.86", text.replace(",", "."))  # 100/35 → два знака
        self.assertIn("test warning", text)


if __name__ == "__main__":
    unittest.main()
