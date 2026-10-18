# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from bot.sources_catalog import _ENTRIES, _TITLES, format_sources_message


class TestSourcesCatalog(unittest.TestCase):
    def test_categories_are_separate_blocks(self) -> None:
        text = format_sources_message()
        titles = [title for _key, title in _TITLES]
        blocks = text.split("\n\n")
        self.assertEqual([block.split("\n", 1)[0] for block in blocks], titles)
        self.assertNotIn(" + ", text)
        self.assertNotIn(" — ", text)

    def test_each_source_is_its_own_line(self) -> None:
        text = format_sources_message()
        lines = text.splitlines()
        bullets = [line for line in lines if line.startswith("• ")]
        self.assertEqual(len(bullets), len(_ENTRIES))
        self.assertTrue(all("http" not in line for line in bullets))
        names = {name for _cat, name, _urls in _ENTRIES}
        self.assertEqual({line[2:] for line in bullets}, names)

    def test_names_sorted_inside_category(self) -> None:
        text = format_sources_message()
        for block in text.split("\n\n"):
            names = [line[2:] for line in block.splitlines() if line.startswith("• ")]
            self.assertEqual(names, sorted(names, key=str.casefold))

    def test_urls_follow_their_source(self) -> None:
        text = format_sources_message()
        self.assertIn(
            "• РСХБ UnionPay\n"
            "https://www.rshb.ru/\n"
            "https://www.unionpayintl.com/\n"
            "https://www.moex.com/",
            text,
        )
        self.assertIn(
            "• Bereza\nhttps://bereza-exchange.com/\nhttps://t.me/bereza_exchange/1631",
            text,
        )
        self.assertIn("• XAACTA\nhttps://t.me/XAACTAExchange", text)
        self.assertIn("• Moreta\nhttps://moretapay.com/", text)
        self.assertIn("• Обувь-Сити\nhttps://vernadka-kassa.ru/", text)
        self.assertLess(len(text), 4096)


if __name__ == "__main__":
    unittest.main()
