# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from sources import cash_aggregate as ca
from sources.vbr_cash import build_vbr_rates_url, vbr_sell_rows


class TestBuildVbrUrl(unittest.TestCase):
    def test_moskva_has_sort_and_subdomain(self) -> None:
        u = build_vbr_rates_url("moskva", "USD")
        self.assertIsNotNone(u)
        assert u is not None
        self.assertIn("moskva.vbr.ru", u)
        self.assertIn("sortType=1", u)
        self.assertIn("sortDirection=0", u)
        self.assertIn("locationNearby=false", u)

    def test_spb_www_geo(self) -> None:
        u = build_vbr_rates_url("sankt-peterburg", "EUR")
        self.assertIsNotNone(u)
        assert u is not None
        self.assertIn("www.vbr.ru", u)
        self.assertIn("locationNearby=true", u)
        self.assertIn("59.9222015", u)
        self.assertIn("30.3398645", u)

    def test_krasnoyarsk_slug(self) -> None:
        u = build_vbr_rates_url("krasnoyarsk", "CNY")
        self.assertIsNotNone(u)
        assert u is not None
        self.assertIn("krasnojarsk.vbr.ru", u)


class TestVbrSellRows(unittest.TestCase):
    def test_first_column_used(self) -> None:
        html = (
            '<tr name="RatesTableExpand" data-bank-alias="t">'
            '<td><span class="rates-name-bank"> Камкомбанк </span></td>'
            '<td class="rates-val -x" data-col="USD">'
            '<div class="rates-calc-block">78,89 ₽</div></td>'
            '<td class="rates-val" data-col="USD">'
            '<div class="rates-calc-block">99,99 ₽</div></td></tr>'
        )
        rows = vbr_sell_rows(html, "USD")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][0], 78.89)
        self.assertEqual(rows[0][1], "Камкомбанк")

    def test_data_col_before_class(self) -> None:
        html = (
            '<tr name="RatesTableExpand" data-bank-alias="x">'
            '<td data-col="EUR" class="rates-val xx">'
            '<div class="rates-calc-block">10,5 ₽</div></td></tr>'
        )
        rows = vbr_sell_rows(html, "EUR")
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0][0], 10.5)


class TestMergeThreeLayers(unittest.TestCase):
    def test_unions_sources(self) -> None:
        banki = [ca.CashOffer(80.0, "Камкомбанк", frozenset({"banki"}))]
        rbc = [ca.CashOffer(80.0, "Камкомбанк", frozenset({"rbc"}))]
        vbr = [ca.CashOffer(80.0, "Камкомбанк", frozenset({"vbr"}))]
        m = ca._merge_offer_layers(banki, rbc, vbr)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].sources, frozenset({"banki", "rbc", "vbr"}))
        self.assertIn("VBR", m[0].sources_label())
        self.assertIn("Banki", m[0].sources_label())
        self.assertIn("РБК", m[0].sources_label())


if __name__ == "__main__":
    unittest.main()
