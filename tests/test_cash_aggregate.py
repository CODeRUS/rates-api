# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from sources.banki_cash import banki_sell_rows
from sources.cash_aggregate import (
    CashOffer,
    _merge_rbc_and_banki,
    _offers_from_banki_payload,
    _offers_from_rbc_banks,
)
from sources.rbc_bank_title import canonical_bank_key


class TestCanonicalBankKey(unittest.TestCase):
    def test_aligns_rbc_style_and_banki_caps(self):
        self.assertEqual(
            canonical_bank_key('АО «Камкомбанк» ОО № 1'),
            canonical_bank_key("КАМКОМБАНК"),
        )


class TestBankiSellRows(unittest.TestCase):
    def test_parses_list(self):
        payload = {
            "list": [
                {
                    "name": "Экспобанк",
                    "exchange": {"sale": 82.8, "buy": 82},
                },
                {"name": "Skip", "exchange": {}},
            ]
        }
        rows = banki_sell_rows(payload)
        self.assertEqual(rows, [(82.8, "Экспобанк")])


class TestMergeRbcBanki(unittest.TestCase):
    def test_dedupes_same_bank_same_rate(self):
        rbc = _offers_from_rbc_banks(
            [
                {
                    "name": 'ООО «Камкомбанк» ДО тест',
                    "rate": {"sell": "80.00"},
                },
            ]
        )
        banki_raw = {
            "list": [
                {"name": "КАМКОМБАНК", "exchange": {"sale": 80.0}},
            ]
        }
        banki = _offers_from_banki_payload(banki_raw)
        merged = _merge_rbc_and_banki(rbc, banki)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].sell, 80.0)
        self.assertEqual(merged[0].sources, frozenset({"rbc", "banki"}))
        self.assertEqual(merged[0].bank_display, "Камкомбанк")

    def test_keeps_two_rows_different_rates(self):
        rbc = [
            CashOffer(80.0, "А", frozenset({"rbc"})),
        ]
        banki = [
            CashOffer(81.0, "Б", frozenset({"banki"})),
        ]
        merged = _merge_rbc_and_banki(rbc, banki)
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
