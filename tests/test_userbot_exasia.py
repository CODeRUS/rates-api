# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from userbot.parser import compile_rules, parse_message
from userbot.sources_config import USERBOT_SOURCES


_EXASIA_SAMPLE = """
👉 АКТУАЛЬНЫЙ КУРС В КАНАЛЕ TELEGRAM

🇷🇺RUB // Баты - 2.65 > (от2k до3k бат)🇹🇭
🇷🇺RUB // Баты - 2.57 - (от3k до 7k бат)🇹🇭
🇷🇺RUB // Баты - 2.54 < (от7k бат)🇹🇭
🇷🇺RUB // Баты - 2.49 < (от20k бат)🇹🇭
🇷🇺RUB // Баты - 2.44 < (от50k бат/инд-й)🇹🇭
"""


class TestUserbotExasia(unittest.TestCase):
    def test_exasia_config_parses_20k_tier(self):
        cfg = next(s for s in USERBOT_SOURCES if s.source_id == "exasia_exthailand")
        self.assertEqual(cfg.chat, "@exthailand")
        self.assertEqual(cfg.name, "Exasia")
        rules = compile_rules(cfg)
        rows = parse_message(
            source_id=cfg.source_id,
            source_name=cfg.name,
            chat=cfg.chat,
            city=cfg.city,
            rules=rules,
            text=_EXASIA_SAMPLE,
            message_id=1,
            message_unix=0.0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].currency, "RUBTHB")
        self.assertEqual(rows[0].category, "exchanger")
        self.assertAlmostEqual(rows[0].rate, 2.49)


if __name__ == "__main__":
    unittest.main()
