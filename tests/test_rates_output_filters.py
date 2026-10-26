# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from rates_categories import SourceCategory
from rates_output_filters import (
    PRESET_NAMES,
    apply_summary_row_filter,
    apply_summary_warning_filter,
)
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
        out = apply_summary_row_filter(rows, "travelask")
        labs = [r.label for r in out]
        self.assertIn("Forex", labs)
        self.assertIn("Other", labs)
        self.assertNotIn("IT Обмен", labs)
        self.assertNotIn("Fly Currency", labs)

    def test_preset_note_match(self) -> None:
        rows = [
            RateRow(2.7, "X", "🤑", note="как IT Обмен в Паттайе", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "travelask")
        self.assertEqual(len(out), 0)

    def test_preset_drops_bybit_unired_casefold(self) -> None:
        rows = [
            RateRow(2.5, "Bybit P2P (cash) → Bitkub", "💸", category=SourceCategory.TRANSFER),
            RateRow(2.4, "BYBIT test", "💸", category=SourceCategory.TRANSFER),
            RateRow(2.6, "Unired RUB → USD → Bank", "💱", category=SourceCategory.TRANSFER),
            RateRow(2.7, "Other", "💱", category=SourceCategory.TRANSFER),
        ]
        out = apply_summary_row_filter(rows, "travelask")
        self.assertEqual([r.label for r in out], ["Other"])

    def test_preset_names_contains_travelask(self) -> None:
        self.assertIn("travelask", PRESET_NAMES)

    def test_ta_alias_same_as_numeric_id(self) -> None:
        self.assertIn("ta", PRESET_NAMES)
        rows = [
            RateRow(2.7, "IT Обмен", "🤑", category=SourceCategory.TRANSFER),
        ]
        self.assertEqual(len(apply_summary_row_filter(rows, "ta")), 0)

    def test_travelask_drops_xaacta_senate_moreta_kwikpay_avosend(self) -> None:
        rows = [
            RateRow(2.4, "Forex", "📈", is_baseline=True),
            RateRow(2.5, "XAACTA", "🤑", category=SourceCategory.EXCHANGER),
            RateRow(2.6, "Senate", "🤑", note="от 10 000 THB", category=SourceCategory.EXCHANGER),
            RateRow(2.7, "Moreta QR Business", "📲", category=SourceCategory.TRANSFER),
            RateRow(2.8, "Moreta QR Merchant", "📲", category=SourceCategory.TRANSFER),
            RateRow(2.9, "KwikPay карта", "💱", category=SourceCategory.TRANSFER),
            RateRow(3.0, "KwikPay счёт", "💱", category=SourceCategory.TRANSFER),
            RateRow(3.1, "Avosend получение в Big C", "💱", category=SourceCategory.TRANSFER),
            RateRow(3.2, "Avosend RUB → USD → Bank", "💱", category=SourceCategory.TRANSFER),
            RateRow(3.3, "askmoney", "🤑", category=SourceCategory.EXCHANGER),
        ]
        labs = [r.label for r in apply_summary_row_filter(rows, "ta")]
        self.assertEqual(labs, ["Forex", "askmoney"])

    def test_travelask_drops_matching_warnings(self) -> None:
        warnings = [
            "xaacta: XAACTA HTTP 400: AMOUNT_OUT_OF_RANGE",
            "senate: опубликованный курс для сумм от 10000 THB, запрошено 800",
            "KwikPay: timeout",
            "Avosend Big C: нет курса",
            "Bitkub: нет highestBid для USDT",
        ]
        self.assertEqual(
            apply_summary_warning_filter(warnings, "travelask"),
            ["Bitkub: нет highestBid для USDT"],
        )
        self.assertEqual(apply_summary_warning_filter(warnings, ""), warnings)


if __name__ == "__main__":
    unittest.main()
