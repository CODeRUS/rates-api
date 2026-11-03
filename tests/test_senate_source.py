# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest import mock

from rates_sources import FetchContext, SourceCategory
from sources.senate import (
    add_percent,
    rub_per_thb_from_pair,
    rub_per_thb_from_prices,
    summary,
    thb_per_base_from_pair,
    thb_per_usdt_atm_from_prices,
)


def _ctx(*, receiving_thb: float | None = 30_000.0) -> FetchContext:
    return FetchContext(
        thb_ref=30_000.0,
        atm_fee=250.0,
        korona_small_rub=100_000.0,
        korona_large_thb=40_000.0,
        avosend_rub=50_000.0,
        unionpay_date=None,
        moex_override=None,
        receiving_thb=receiving_thb,
        warnings=[],
    )


_PRICES_PAYLOAD = {
    "rub_for_usdt_buy": {"price": 92.5},
    "thb_for_usdt_sell": {"price": 32.65},
}


class TestSenateSource(unittest.TestCase):
    def test_add_percent_matches_site_formula(self) -> None:
        self.assertAlmostEqual(add_percent(100.0, 2.5), 102.5)

    def test_rub_per_thb_from_prices(self) -> None:
        base = 92.5 / 32.65
        expected = add_percent(base, 2.5)
        self.assertAlmostEqual(rub_per_thb_from_prices(_PRICES_PAYLOAD), expected)

    def test_thb_per_usdt_atm_from_prices(self) -> None:
        self.assertAlmostEqual(
            thb_per_usdt_atm_from_prices(_PRICES_PAYLOAD),
            add_percent(32.65, -2.5),
        )

    def test_ratio_is_rub_per_thb_when_client_is_not_base(self) -> None:
        self.assertAlmostEqual(
            rub_per_thb_from_pair({"ratio": 2.5, "is_client_base": False}),
            2.5,
        )

    def test_usdt_thb_is_ratio_when_client_is_base(self) -> None:
        self.assertAlmostEqual(
            thb_per_base_from_pair({"ratio": 32.6676375, "is_client_base": True}),
            32.6676375,
        )

    def test_ratio_inverts_when_client_is_base(self) -> None:
        self.assertAlmostEqual(
            rub_per_thb_from_pair({"ratio": 0.4, "is_client_base": True}),
            2.5,
        )

    @mock.patch("sources.senate.fetch_prices", return_value=_PRICES_PAYLOAD)
    def test_summary_quote(self, _fetch) -> None:
        ctx = _ctx()
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(rows[0].label, "Senate")
        self.assertEqual(rows[0].category, SourceCategory.EXCHANGER)
        self.assertEqual(rows[0].note, "от 10 000 THB")
        self.assertAlmostEqual(
            rows[0].rate,
            rub_per_thb_from_prices(_PRICES_PAYLOAD),
        )
        self.assertEqual(ctx.warnings, [])

    @mock.patch("sources.senate.fetch_prices", return_value=_PRICES_PAYLOAD)
    def test_summary_warns_below_published_minimum(self, _fetch) -> None:
        ctx = _ctx(receiving_thb=5_000.0)
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("10000", ctx.warnings[0])

    @mock.patch("sources.senate.fetch_prices", side_effect=RuntimeError("prices HTTP 500"))
    def test_summary_warning_on_error(self, _fetch) -> None:
        ctx = _ctx()
        self.assertIsNone(summary(ctx))
        self.assertIn("HTTP 500", ctx.warnings[0])


if __name__ == "__main__":
    unittest.main()
