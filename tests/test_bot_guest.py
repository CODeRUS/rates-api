# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from bot.guest import (
    GUEST_DENIED,
    GUEST_TEXT_LIMIT,
    fit_guest_text,
    prepare_guest_command,
    strip_bot_mention,
)

BOT = "RatesBot"


class TestStripBotMention(unittest.TestCase):
    def test_mention_before_command(self) -> None:
        self.assertEqual(
            strip_bot_mention("@RatesBot /rates 30000", BOT),
            "/rates 30000",
        )

    def test_command_before_mention(self) -> None:
        self.assertEqual(
            strip_bot_mention("/rates 30000 @ratesbot", BOT),
            "/rates 30000",
        )

    def test_command_suffix(self) -> None:
        self.assertEqual(strip_bot_mention("/rates@RatesBot ta", BOT), "/rates ta")

    def test_mention_only(self) -> None:
        self.assertEqual(strip_bot_mention("@RatesBot", BOT), "")


class TestPrepareGuestCommand(unittest.TestCase):
    def test_rates_after_mention_with_amount(self) -> None:
        cmd = prepare_guest_command("@RatesBot /rates 30000", BOT)
        self.assertEqual(cmd.kind, "rates")
        self.assertEqual(cmd.payload["receiving_thb"], 30000.0)
        self.assertEqual(cmd.payload["output_filter"], "")
        self.assertNotIn("refresh", cmd.payload)

    def test_rates_without_slash(self) -> None:
        cmd = prepare_guest_command("@ratesbot rates", BOT)
        self.assertEqual(cmd.kind, "rates")
        self.assertEqual(cmd.payload["output_filter"], "")
        self.assertIsNone(cmd.payload["receiving_thb"])

    def test_mention_not_leading(self) -> None:
        cmd = prepare_guest_command("/cash 1 @RatesBot banki", BOT)
        self.assertEqual(cmd.kind, "cash")
        self.assertEqual(cmd.payload["use_banki"], True)
        self.assertEqual(cmd.payload["use_rbc"], False)
        self.assertEqual(cmd.payload["use_vbr"], False)

    def test_empty_is_silent(self) -> None:
        cmd = prepare_guest_command("@RatesBot", BOT)
        self.assertEqual(cmd.kind, "ignore")
        self.assertEqual(cmd.reply, "")

    def test_plain_text_is_silent(self) -> None:
        cmd = prepare_guest_command("@RatesBot привет", BOT)
        self.assertEqual(cmd.kind, "ignore")
        self.assertEqual(cmd.reply, "")

    def test_start_is_silent(self) -> None:
        cmd = prepare_guest_command("/start@RatesBot", BOT)
        self.assertEqual(cmd.kind, "ignore")
        self.assertEqual(cmd.reply, "")

    def test_refresh_denied(self) -> None:
        cmd = prepare_guest_command("@RatesBot /refresh usdt", BOT)
        self.assertEqual(cmd.kind, "deny")
        self.assertEqual(cmd.reply, GUEST_DENIED)

    def test_gpt_denied(self) -> None:
        cmd = prepare_guest_command("gpt @ratesbot", BOT)
        self.assertEqual(cmd.kind, "deny")

    def test_rates_refresh_token_ignored(self) -> None:
        cmd = prepare_guest_command("@RatesBot /rates refresh ta", BOT)
        self.assertEqual(cmd.kind, "rates")
        self.assertEqual(cmd.payload["output_filter"], "ta")
        self.assertNotIn("refresh", cmd.payload)

    def test_calc_format_error(self) -> None:
        cmd = prepare_guest_command("/calc@RatesBot", BOT)
        self.assertEqual(cmd.kind, "error")
        self.assertIn("/calc", cmd.reply)

    def test_cash_format_error(self) -> None:
        cmd = prepare_guest_command("@RatesBot /cash abc", BOT)
        self.assertEqual(cmd.kind, "error")
        self.assertIn("/cash", cmd.reply)


class TestFitGuestText(unittest.TestCase):
    def test_keeps_short_text(self) -> None:
        self.assertEqual(fit_guest_text("ok"), "ok")

    def test_cuts_on_line_boundary(self) -> None:
        line = "a" * 100
        text = "\n".join([line] * 50)
        out = fit_guest_text(text, limit=GUEST_TEXT_LIMIT)
        self.assertLessEqual(len(out), GUEST_TEXT_LIMIT)
        self.assertGreater(len(text), GUEST_TEXT_LIMIT)
        self.assertTrue(out)
        self.assertTrue(all(part == line for part in out.split("\n")))
        self.assertLess(len(out.split("\n")), 50)


if __name__ == "__main__":
    unittest.main()
