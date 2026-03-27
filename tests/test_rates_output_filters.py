# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from rates_categories import SourceCategory
from rates_output_filters import PRESET_NAMES, apply_summary_row_filter
from rates_sources import RateRow


class TestRatesOutputFilters(unittest.TestCase):
    def test_unknown_filter_noop(self) -> None:
        rows = [
            RateRow(1.0, "IT Обмен", "•", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "no_such_preset_ever")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].label, "IT Обмен")

    def test_empty_filter_noop(self) -> None:
        rows = [
            RateRow(1.0, "IT Обмен", "•", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "")
        self.assertEqual(len(out), 1)
        out2 = apply_summary_row_filter(rows, "   ")
        self.assertEqual(len(out2), 1)

    def test_preset_161665026_drops_substrings_keeps_baseline(self) -> None:
        rows = [
            RateRow(2.5, "Forex", "📈", is_baseline=True),
            RateRow(2.7, "IT Обмен", "🤑", category=SourceCategory.TRANSFER),
            RateRow(2.71, "Fly Currency", "🤑", category=SourceCategory.TRANSFER),
            RateRow(2.6, "Other", "💱", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "161665026")
        labs = [r.label for r in out]
        self.assertIn("Forex", labs)
        self.assertIn("Other", labs)
        self.assertNotIn("IT Обмен", labs)
        self.assertNotIn("Fly Currency", labs)

    def test_preset_note_match(self) -> None:
        rows = [
            RateRow(2.7, "X", "🤑", note="как IT Обмен в Паттайе", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "161665026")
        self.assertEqual(len(out), 0)

    def test_preset_drops_bybit_unired_casefold(self) -> None:
        rows = [
            RateRow(2.5, "Bybit P2P (cash) → Bitkub", "💸", category=SourceCategory.TRANSFER),
            RateRow(2.4, "BYBIT test", "💸", category=SourceCategory.TRANSFER),
            RateRow(2.6, "Unired RUB → USD → Bank", "💱", category=SourceCategory.TRANSFER),
            RateRow(2.7, "Other", "💱", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "161665026")
        self.assertEqual([r.label for r in out], ["Other"])

    def test_preset_names_contains_telegram_id(self) -> None:
        self.assertIn("161665026", PRESET_NAMES)

    def test_ta_alias_same_as_numeric_id(self) -> None:
        self.assertIn("ta", PRESET_NAMES)
        rows = [
            RateRow(2.7, "IT Обмен", "🤑", category=SourceCategory.TRANSFER),
        ]
        self.assertEqual(len(apply_summary_row_filter(rows, "ta")), 0)


if __name__ == "__main__":
    unittest.main()
