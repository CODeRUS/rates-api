# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from sources.rbc_bank_title import rbc_short_bank_name
from sources.rbc_cash_json import bank_sell_rows, top_sell_offers


class TestRbcBankTitle(unittest.TestCase):
    def test_user_examples(self):
        self.assertEqual(
            rbc_short_bank_name('АО КБ "ЮНИСТРИМ" ОО № 193'),
            "ЮНИСТРИМ",
        )
        self.assertEqual(
            rbc_short_bank_name(
                'АО "Реалист Банк" (бывший "БайкалИнвестБанк") ДО "Центральный"'
            ),
            "Реалист Банк",
        )
        self.assertEqual(
            rbc_short_bank_name('ООО КБЭР "Банк Казани" ДО "Таганская"'),
            "Банк Казани",
        )
        self.assertEqual(
            rbc_short_bank_name(
                'ООО «Камкомбанк» ул. Ярослава Гашека, д.5, лит А, пом. 5-Н'
            ),
            "Камкомбанк",
        )


class TestTopSellOffers(unittest.TestCase):
    def test_sorted_by_sell_ascending(self):
        banks = [
            {"name": 'ООО "High" ДО 1', "rate": {"sell": "85"}},
            {"name": 'АО "Low" ДО 2', "rate": {"sell": "80.5"}},
            {"name": 'ПАО "Mid" ДО', "rate": {"sell": "82"}},
            {"name": "bad", "rate": {}},
        ]
        rows = bank_sell_rows(banks)
        self.assertEqual([r[0] for r in rows], [80.5, 82.0, 85.0])
        top = top_sell_offers(banks, n=3)
        self.assertEqual(len(top), 3)
        self.assertEqual([t[0] for t in top], [80.5, 82.0, 85.0])
        self.assertEqual(top[0][2], "Low")

    def test_dedupes_same_bank_same_rate(self):
        banks = [
            {"name": 'АО КБ "ЮНИСТРИМ" ОО № 1', "rate": {"sell": "80.85"}},
            {"name": 'АО КБ "ЮНИСТРИМ" ОО № 2', "rate": {"sell": "80.85"}},
            {"name": 'АО "Other" ДО', "rate": {"sell": "81"}},
            {"name": 'ПАО "Third" ДО', "rate": {"sell": "82"}},
        ]
        top = top_sell_offers(banks, n=3)
        self.assertEqual(len(top), 3)
        self.assertEqual([t[2] for t in top], ["ЮНИСТРИМ", "Other", "Third"])


if __name__ == "__main__":
    unittest.main()
