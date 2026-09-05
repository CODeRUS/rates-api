# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from userbot.parser import compile_rules, parse_message
from userbot.sources_config import USERBOT_SOURCES


_EXASIA_SAMPLE_BATH = """
👉 АКТУАЛЬНЫЙ КУРС В КАНАЛЕ TELEGRAM

🇷🇺RUB // Баты - 2.65 > (от2k до3k бат)🇹🇭
🇷🇺RUB // Баты - 2.57 - (от3k до 7k бат)🇹🇭
🇷🇺RUB // Баты - 2.54 < (от7k бат)🇹🇭
🇷🇺RUB // Баты - 2.49 < (от20k бат)🇹🇭
🇷🇺RUB // Баты - 2.44 < (от50k бат/инд-й)🇹🇭
"""

_EXASIA_SAMPLE_THB = """
👉 АКТУАЛЬНЫЙ КУРС В КАНАЛЕ TELEGRAM

🇷🇺RUB // THB -  2.96  > (от1k до 3k THB)🇹🇭
🇷🇺RUB // THB -  2.87  - (от3k THB)🇹🇭
🇷🇺RUB // THB -  2.85  < (от8k THB)🇹🇭
🇷🇺RUB // THB -  2.83  < (от20k THB)🇹🇭
🇷🇺RUB // THB -  2.81  < (от50k THB/best)🇹🇭
"""


def _parse_exasia(text: str):
    cfg = next(s for s in USERBOT_SOURCES if s.source_id == "exasia_exthailand")
    return parse_message(
        source_id=cfg.source_id,
        source_name=cfg.name,
        chat=cfg.chat,
        city=cfg.city,
        rules=compile_rules(cfg),
        text=text,
        message_id=1,
        message_unix=0.0,
    )


class TestUserbotExasia(unittest.TestCase):
    def test_exasia_config_parses_legacy_20k_bat(self):
        cfg = next(s for s in USERBOT_SOURCES if s.source_id == "exasia_exthailand")
        self.assertEqual(cfg.chat, "@exthailand")
        self.assertEqual(cfg.name, "Exasia")
        rows = _parse_exasia(_EXASIA_SAMPLE_BATH)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].currency, "RUBTHB")
        self.assertEqual(rows[0].category, "exchanger")
        self.assertAlmostEqual(rows[0].rate, 2.49)

    def test_exasia_config_parses_current_20k_thb(self):
        rows = _parse_exasia(_EXASIA_SAMPLE_THB)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].rate, 2.83)


if __name__ == "__main__":
    unittest.main()
