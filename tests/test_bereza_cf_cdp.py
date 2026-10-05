# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from browser.bereza_cf_cdp import clearance_usable, debug_url_from_cmdline, find_bereza_tab


class TestBerezaCfExport(unittest.TestCase):
    def test_cached_title_without_token_is_rejected(self) -> None:
        self.assertFalse(clearance_usable("Bereza Exchange", 403, "cookie"))
        self.assertFalse(clearance_usable("Just a moment...", 200, "cookie"))
        self.assertFalse(clearance_usable("Bereza Exchange", 200, "  "))
        self.assertFalse(clearance_usable("", 200, "cookie"))

    def test_live_token_is_accepted(self) -> None:
        self.assertTrue(clearance_usable("Bereza Exchange", 200, "cookie"))

    def test_find_bereza_tab_skips_other_pages(self) -> None:
        tabs = [
            {"type": "page", "url": "https://multitransfer.ru/transfer/thailand"},
            {"type": "page", "url": "https://bereza-exchange.com/"},
        ]
        found = find_bereza_tab(tabs)
        self.assertEqual(found["url"], "https://bereza-exchange.com/")
        self.assertIsNone(find_bereza_tab(tabs[:1]))

    def test_debug_url_from_cmdline(self) -> None:
        self.assertEqual(
            debug_url_from_cmdline([b"chromium", b"--remote-debugging-port=9222"]),
            "http://127.0.0.1:9222",
        )
        self.assertEqual(
            debug_url_from_cmdline(
                b"chromium-browser --window-size=1920,1080 --remote-debugging-port=9222\x00"
            ),
            "http://127.0.0.1:9222",
        )
        self.assertIsNone(debug_url_from_cmdline([b"chromium"]))
