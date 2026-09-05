# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from userbot.chats import event_chat_keys, keys_from_event, lookup_source, normalize_chat_ref, peer_id_keys
from userbot.parser import compile_rules, parse_message
from userbot.sources_config import USERBOT_SOURCES


_FINTRUST_SAMPLE = """
Актуальный курс обмена валют

⚠️ Курсы действительны на момент публикации
💼 Обмен валют от 10.000$€

💵 Покупка 🔵 87.00- ⚪️ 85.80
💵 Продажа 🔵 87.90- ⚪️ 87.60
💸 Покупка 100.70-Продажа 101.50
💶 Покупка 100.20-101.20 (500)
💸 Покупка 12.50-13.40
"""


class TestUserbotChats(unittest.TestCase):
    def test_normalize_username(self):
        self.assertEqual(normalize_chat_ref("@FinTrust"), "@fintrust")
        self.assertEqual(normalize_chat_ref("-100123"), "-100123")

    def test_peer_id_keys_channel_marked(self):
        keys = peer_id_keys(-1001725670916)
        self.assertIn("-1001725670916", keys)
        self.assertIn("1725670916", keys)

    def test_peer_id_keys_channel_raw(self):
        keys = peer_id_keys(1725670916)
        self.assertIn("1725670916", keys)
        self.assertIn("-1001725670916", keys)

    def test_lookup_by_marked_id_without_username(self):
        cfg = object()
        idx = {"@fintrust": cfg, "-1001500647848": cfg, "1500647848": cfg}
        keys = event_chat_keys(username=None, chat_id=-1001500647848)
        self.assertIs(lookup_source(idx, keys), cfg)

    def test_event_lookup_keys_chat_id_only(self):
        event = SimpleNamespace(chat=None, chat_id=-1001500647848)
        keys = keys_from_event(event)
        self.assertIn("-1001500647848", keys)
        self.assertIn("1500647848", keys)

    def test_fintrust_current_post(self):
        cfg = next(s for s in USERBOT_SOURCES if s.source_id == "fintrust_exchange")
        rows = parse_message(
            source_id=cfg.source_id,
            source_name=cfg.name,
            chat=cfg.chat,
            city=cfg.city,
            rules=compile_rules(cfg),
            text=_FINTRUST_SAMPLE,
            message_id=1,
            message_unix=0.0,
        )
        by_ccy = {r.currency: r.rate for r in rows}
        self.assertAlmostEqual(by_ccy["USD"], 87.60)
        self.assertAlmostEqual(by_ccy["EUR"], 101.20)


if __name__ == "__main__":
    unittest.main()
