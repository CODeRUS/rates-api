# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from sources.unired_bkb import bbl_latest_fx as bbl
from sources.unired_bkb import unired_tg_preview as utg


class TestUniredBkb(unittest.TestCase):
    def test_parse_usd50_tt(self) -> None:
        rows = [{"Family": "EUR20"}, {"Family": "USD50", "TT": "32,52"}]
        self.assertAlmostEqual(bbl.parse_usd50_tt_thb(rows), 32.52)

    def test_extract_unired_from_html_snippet(self) -> None:
        html = """
        <div class="tgme_widget_message_text js-message_text">old</div>
        <div class="tgme_widget_message_text js-message_text">
        Россиядан - VISAга 💳<br/><b>1 &#036; = 81,98 RUB</b>
        </div>
        """
        self.assertAlmostEqual(utg.extract_latest_usd_rub_from_html(html), 81.98)

    def test_cross_implied(self) -> None:
        rub_usd = 81.98
        thb_usd = 32.52
        self.assertAlmostEqual(rub_usd / thb_usd, 2.521, places=3)


if __name__ == "__main__":
    unittest.main()
