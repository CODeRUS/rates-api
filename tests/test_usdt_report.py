# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import usdt_report


class TestUsdtReport(unittest.TestCase):
    def test_parse_bereza_usdt_line(self):
        sample = (
            "💶  RUB (₽) -➡️ THB (฿) = 2.55\n"
            "💲  USDT (₮) -➡️ THB (฿) = 31.33\n"
            "ПРИМЕР: 255.000₽ ≈ 100.000 бат\n"
        )
        self.assertEqual(usdt_report._parse_bereza_usdt_from_text(sample), 31.33)

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
                "bereza_bid": 31.33,
            },
        }
        warnings = ["test warning"]
        text = usdt_report.format_usdt_report_text(data, warnings)
        self.assertIn("100.00 | Bybit (наличные)", text)
        self.assertIn("34.90 | Binance TH (bid)", text)
        self.assertIn("31.33 | Bereza Exchange (Telegram)", text)
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
            if title == "THB за 1 USDT":
                self.assertEqual(vals, sorted(vals, reverse=True), msg=title)
            else:
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
