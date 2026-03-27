# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from bot.rates_tokens import parse_rates_command_tokens


class TestParseRatesCommandTokens(unittest.TestCase):
    def test_ta_shorthand(self) -> None:
        r, f = parse_rates_command_tokens(["/rates", "ta"])
        self.assertFalse(r)
        self.assertEqual(f, "ta")

    def test_filter_ta_explicit(self) -> None:
        r, f = parse_rates_command_tokens(["/rates", "filter", "ta"])
        self.assertFalse(r)
        self.assertEqual(f, "ta")

    def test_refresh_and_ta_any_order(self) -> None:
        r, f = parse_rates_command_tokens(["/rates", "r", "ta"])
        self.assertTrue(r)
        self.assertEqual(f, "ta")
        r2, f2 = parse_rates_command_tokens(["/rates", "ta", "refresh"])
        self.assertTrue(r2)
        self.assertEqual(f2, "ta")

    def test_only_refresh(self) -> None:
        r, f = parse_rates_command_tokens(["/rates", "refresh"])
        self.assertTrue(r)
        self.assertEqual(f, "")

    def test_bot_username_prefix(self) -> None:
        r, f = parse_rates_command_tokens(["/rates@SomeBot", "ta"])
        self.assertFalse(r)
        self.assertEqual(f, "ta")


if __name__ == "__main__":
    unittest.main()
