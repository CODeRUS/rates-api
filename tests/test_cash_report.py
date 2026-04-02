# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import cash_report
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

    def test_trim_address_without_quotes_at_comma_or_dot(self):
        self.assertEqual(
            rbc_short_bank_name(
                "Банк Казани. ул. Большая Московская, д. 18 / Разъезжая, д. 12"
            ),
            "Банк Казани",
        )
        self.assertEqual(
            rbc_short_bank_name("ПАО ПримерБанк, филиал Центральный, г. Тула"),
            "ПримерБанк",
        )


class TestFindBestPlainCashL2(unittest.TestCase):
    def test_skips_when_cached_top_smaller_than_requested(self):
        body = (
            "Наличные\n\n"
            "USD Москва\n"
            "80.0 | — | A (VBR)\n"
            "81.0 | — | B (VBR)\n"
            "82.0 | — | C (VBR)\n"
            "\n"
        )
        doc = {
            "l2": {
                "l2:cash:stub": {
                    "text": body,
                    "payload": {
                        "top_n": 3,
                        "use_rbc": False,
                        "use_banki": False,
                        "use_vbr": True,
                    },
                    "saved_unix": 100.0,
                    "deps": {},
                }
            }
        }
        k = cash_report._find_best_plain_cash_l2_key_for_city(
            doc,
            "Москва",
            top_n=20,
            use_rbc=False,
            use_banki=False,
            use_vbr=True,
        )
        self.assertIsNone(k)

    def test_picks_when_cached_top_sufficient(self):
        lines = "\n".join(
            [f"{80.0 + i:.1f} | — | B{i} (VBR)" for i in range(8)]
        )
        body = f"Наличные\n\nUSD Москва\n{lines}\n\n"
        doc = {
            "l2": {
                "l2:cash:stub": {
                    "text": body,
                    "payload": {
                        "top_n": 20,
                        "use_rbc": False,
                        "use_banki": False,
                        "use_vbr": True,
                    },
                    "saved_unix": 100.0,
                    "deps": {},
                }
            }
        }
        k = cash_report._find_best_plain_cash_l2_key_for_city(
            doc,
            "Москва",
            top_n=8,
            use_rbc=False,
            use_banki=False,
            use_vbr=True,
        )
        self.assertEqual(k, "l2:cash:stub")


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
