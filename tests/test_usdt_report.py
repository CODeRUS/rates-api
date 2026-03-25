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
        self.assertIn("100.00 | Bybit (наличные)", text)
        self.assertIn("34.90 | Binance TH (bid)", text)
        self.assertIn("2.81 | HTX P2P (перевод) → Bitkub", text)  # мин. RUB/THB при сортировке

        for title, next_title in (
            ("RUB за 1 USDT (P2P, лучшая цена)", "THB за 1 USDT"),
            ("THB за 1 USDT", "Полные пути:"),
        ):
            block = text.split(title, 1)[1].split(next_title, 1)[0]
            vals = [
                float(line.split("|", 1)[0].strip())
                for line in block.strip().split("\n")
                if "|" in line and line.split("|", 1)[0].strip() != "—"
            ]
            self.assertEqual(vals, sorted(vals), msg=title)

        paths_block = text.split("Полные пути:", 1)[1].split("Предупреждения:", 1)[0]
        path_vals = [
            float(line.split("|", 1)[0].strip())
            for line in paths_block.strip().split("\n")
            if "|" in line and line.split("|", 1)[0].strip() != "—"
        ]
        self.assertEqual(path_vals, sorted(path_vals))

        self.assertIn("test warning", text)


if __name__ == "__main__":
    unittest.main()
