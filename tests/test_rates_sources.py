# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import unittest

from rates_sources import (
    FetchContext,
    RateRow,
    RateSource,
    SourceCategory,
    SourceQuote,
    is_cash_category,
    run_sources,
)


def _fake_summary(rows):
    def f(ctx):
        return rows

    return f


class TestRatesSources(unittest.TestCase):
    def test_default_sources_order_and_baseline(self):
        rs = importlib.import_module("rates_sources")
        ids = [s.id for s in rs.DEFAULT_SOURCES]
        self.assertEqual(ids[0], "forex")
        self.assertTrue(rs.DEFAULT_SOURCES[0].is_baseline)
        self.assertEqual(sum(1 for s in rs.DEFAULT_SOURCES if s.is_baseline), 1)
        self.assertEqual(
            ids,
            [
                "forex",
                "rshb_unionpay",
                "bybit_bitkub",
                "korona",
                "avosend",
                "ex24",
                "kwikpay",
                "askmoney",
                "ttexchange",
            ],
        )
        cats = {s.id: s.category for s in rs.DEFAULT_SOURCES}
        self.assertEqual(cats["ttexchange"].name, "TRANSFER")
        for i in ids:
            self.assertEqual(cats[i].name, "TRANSFER")

    def test_run_sources_sort_and_dedup(self):
        base = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        a = RateSource(
            "a",
            "a",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "X"),
                    SourceQuote(2.0, "Y"),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, baseline, w = run_sources(ctx, [base, a])
        self.assertTrue(rows[0].is_baseline)
        self.assertEqual(
            [r.rate for r in rows[1:]],
            sorted([r.rate for r in rows[1:]]),
        )

    def test_run_sources_transfer_block_before_cash(self):
        """После baseline TRANSFER: все TRANSFER по rate, затем все наличные по категориям."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        t = RateSource(
            "t1",
            "t",
            False,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(3.0, "T")]),
        )
        c = RateSource(
            "cash",
            "c",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(1.0, "Cash")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        routes = [forex, c, t]
        rows, _, _ = run_sources(ctx, routes)
        labels = [r.label for r in rows]
        self.assertEqual(labels[0], "Forex")
        i_cash = labels.index("Cash")
        i_t = labels.index("T")
        self.assertLess(i_t, i_cash)

    def test_run_sources_cash_blocks_ordered_by_currency_then_rate(self):
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        fx = RateSource(
            "fx",
            "x",
            False,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.8, "X")]),
        )
        us_hi = RateSource(
            "us",
            "u",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(9.0, "UsdHi")]),
        )
        us_lo = RateSource(
            "us2",
            "u",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(8.0, "UsdLo")]),
        )
        rub = RateSource(
            "rub",
            "r",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(3.0, "Rub")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, fx, us_hi, us_lo, rub])
        labels = [r.label for r in rows]
        self.assertLess(labels.index("Rub"), labels.index("UsdHi"))
        self.assertLess(labels.index("UsdHi"), labels.index("UsdLo"))

    def test_cash_same_category_sorted_by_rate_ignores_source_list_order(self):
        """CASH_USD: по убыванию курса (THB/ед.); порядок не от порядка источников в списке."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        hi_first = RateSource(
            "vendor_high_first",
            "•",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(35.0, "Hi")]),
        )
        lo_second = RateSource(
            "vendor_low_second",
            "•",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(33.0, "Lo")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, hi_first, lo_second])
        cash_usd = [r for r in rows if r.category == SourceCategory.CASH_USD]
        self.assertEqual([r.label for r in cash_usd], ["Hi", "Lo"])
        self.assertEqual([r.rate for r in cash_usd], [35.0, 33.0])

    def test_source_quote_category_overrides_source(self):
        """Котировка может задать категорию наличных, не меняя категорию плагина (TRANSFER)."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        hybrid = RateSource(
            "hybrid",
            "🤑",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "Online"),
                    SourceQuote(
                        3.5,
                        "Cash desk",
                        category=SourceCategory.CASH_RUB,
                    ),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, hybrid])
        labels = [r.label for r in rows]
        self.assertLess(labels.index("Online"), labels.index("Cash desk"))
        cash_row = next(r for r in rows if r.label == "Cash desk")
        self.assertEqual(cash_row.category, SourceCategory.CASH_RUB)
        self.assertTrue(is_cash_category(cash_row.category))
        self.assertTrue(cash_row.compare_to_baseline)

    def test_source_quote_emoji_overrides_source(self):
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        src = RateSource(
            "x",
            "🤑",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "A"),
                    SourceQuote(3.1, "B", emoji="•"),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, src])
        a = next(r for r in rows if r.label == "A")
        b = next(r for r in rows if r.label == "B")
        self.assertEqual(a.emoji, "🤑")
        self.assertEqual(b.emoji, "•")

    def test_parse_ex24_cash_rub_buy_rub_per_thb(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_rub_buy_rub_per_thb

        frag = 'foo\\"RUB\\":{\\"buy\\":\\"0.5\\",\\"sell\\":\\"0.6\\"bar'
        self.assertAlmostEqual(parse_ex24_cash_rub_buy_rub_per_thb(frag), 2.0)

    def test_rate_row_format_without_forex_delta_when_not_comparable(self):
        r = RateRow(
            33.2,
            "Desk",
            "•",
            note="THB/USD",
            category=SourceCategory.CASH_USD,
            compare_to_baseline=False,
        )
        line = r.format_line(2.5)
        self.assertNotIn("%", line)
        self.assertIn("33.200", line)
        self.assertIn("Desk", line)

    def test_parse_ex24_cash_eur_thb_per_eur(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        frag = 'x\\"EUR\\":{\\"buy\\":\\"40.0\\",\\"sell\\":\\"41\\"y'
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "EUR"), 40.0)

    def test_ttexchange_eur_all_tiers_same_rate_omits_denoms(self):
        from sources.ttexchange import _pick_currency_row

        cur = [
            {"name": "EUR(L)", "current_buy_rate": 37.6, "description": "500"},
            {"name": "EUR(M)", "current_buy_rate": 37.6, "description": "200-100"},
            {"name": "EUR(S)", "current_buy_rate": 37.6, "description": "50-5"},
        ]
        row, tier_note, omit = _pick_currency_row(cur, "EUR")
        self.assertIsNotNone(row)
        self.assertEqual(float(row["current_buy_rate"]), 37.6)
        self.assertEqual(tier_note, "")
        self.assertTrue(omit)

    def test_ttexchange_usd_two_tiers_at_max_joins_when_rates_differ_across_tiers(self):
        from sources.ttexchange import _pick_currency_row

        cur = [
            {"name": "USD(L)", "current_buy_rate": 32.5, "description": "100-50"},
            {"name": "USD(M)", "current_buy_rate": 32.5, "description": "20-5"},
            {"name": "USD(S)", "current_buy_rate": 31.0, "description": "2-1"},
        ]
        row, tier_note, omit = _pick_currency_row(cur, "USD")
        self.assertFalse(omit)
        self.assertIn("100-50", tier_note)
        self.assertIn("20-5", tier_note)
        self.assertEqual(float(row["current_buy_rate"]), 32.5)

    def test_parse_ex24_cash_usd_max_buy_across_denoms(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        frag = (
            'tv\\":{\\"USD 1-50\\":{\\"buy\\":\\"31.0\\",'
            '\\"USD 100\\":{\\"buy\\":\\"32.5\\",'
            '\\"USD 100 Old\\":{\\"buy\\":\\"32.0\\"'
        )
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "USD"), 32.5)


if __name__ == "__main__":
    unittest.main()
