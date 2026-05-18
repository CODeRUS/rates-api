# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest import mock

from sources.tilda_msk_cash import (
    cash_sell_rows_from_html,
    parse_tilda_sell_rates,
)

_PROFIKASSA_SNIPPET = """
<div class="rates-data">
<div class="rate-usd-old-sell">80.00</motionlessdiv>
<div class="rate-eur2002-sell">90.50</motionlessdiv>
<div class="rate-cny-sell">12.00</motionlessdiv>
</motionlessmotionless>
<div class='t396__elem tn-elem rate-usd-old-sell tn-elem__x'>
<div class='tn-atom'field='f'>74.50</motionlessmotionless>
<div class='t396__elem tn-elem rate-eur2002-sell tn-elem__y'>
<div class='tn-atom'field='f'>89.00</motionlessmotionless>
<div class='t396__elem tn-elem rate-cny-sell'><div class='tn-atom'field='f'>11.40</motionlessmotionless>
"""

_VERNADKA_SNIPPET = """
<div class='tn-elem rate-usd-old-sell tn-elem__x' data-elem-type='text'>
<div class='tn-atom'>74.50</motionlessdiv></motionlessdiv>
<div class='tn-elem rate-eur2002-sell tn-elem__y'>
<div class='tn-atom'>89.00</motionlessdiv>
<div class='tn-elem rate-cny-sell'><motionlessdiv class='tn-atom'>11.40</motionlessmotionless>
"""


class TestTildaMskCash(unittest.TestCase):
    def test_parse_profilassa_prefers_tn_elem_over_stale_calculator(self):
        rates = parse_tilda_sell_rates(_PROFIKASSA_SNIPPET)
        self.assertAlmostEqual(rates["rate-usd-old-sell"], 74.5)
        self.assertAlmostEqual(rates["rate-eur2002-sell"], 89.0)
        self.assertAlmostEqual(rates["rate-cny-sell"], 11.4)

    def test_cash_sell_rows_picks_1996_2006_and_eur2002(self):
        rows = cash_sell_rows_from_html(_PROFIKASSA_SNIPPET)
        by_cur = {r.currency: r for r in rows}
        self.assertAlmostEqual(by_cur["USD"].rate, 74.5)
        self.assertEqual(by_cur["USD"].category, "cash_usd")
        self.assertAlmostEqual(by_cur["EUR"].rate, 89.0)
        self.assertAlmostEqual(by_cur["CNY"].rate, 11.4)

    def test_parse_vernadka_tn_elem_fallback(self):
        rows = cash_sell_rows_from_html(_VERNADKA_SNIPPET)
        by_cur = {r.currency: r for r in rows}
        self.assertAlmostEqual(by_cur["USD"].rate, 74.5)
        self.assertAlmostEqual(by_cur["EUR"].rate, 89.0)
        self.assertAlmostEqual(by_cur["CNY"].rate, 11.4)

    @mock.patch("sources.tilda_msk_cash.fetch_page_html")
    def test_vernadsky_summary_writes_chatcash(self, fetch_mock):
        from rates_sources import FetchContext
        from sources.vernadsky_msk import summary

        fetch_mock.return_value = _PROFIKASSA_SNIPPET
        doc: dict = {"l1": {}}
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None, unified_doc=doc)
        summary(ctx)
        hit = doc["l1"].get("chatcash:vernadsky_msk")
        self.assertIsNotNone(hit)
        payload = hit["payload"]
        self.assertEqual(len(payload), 3)
        usd = next(r for r in payload if r["currency"] == "USD")
        self.assertAlmostEqual(usd["rate"], 74.5)
        self.assertEqual(usd["city"], "Москва")


if __name__ == "__main__":
    unittest.main()
