# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from browser.bereza_cf_cdp import clearance_usable


class TestBerezaCfExport(unittest.TestCase):
    def test_cached_title_without_token_is_rejected(self) -> None:
        self.assertFalse(clearance_usable("Bereza Exchange", 403, "cookie"))
        self.assertFalse(clearance_usable("Just a moment...", 200, "cookie"))
        self.assertFalse(clearance_usable("Bereza Exchange", 200, "  "))
        self.assertFalse(clearance_usable("", 200, "cookie"))

    def test_live_token_is_accepted(self) -> None:
        self.assertTrue(clearance_usable("Bereza Exchange", 200, "cookie"))
