# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import unittest

from rates_sources import (
    FetchContext,
    RateSource,
    SourceCategory,
    SourceQuote,
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
        self.assertEqual(cats["ttexchange"].name, "CASH")
        for i in ids:
            if i != "ttexchange":
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
        """После baseline TRANSFER: все TRANSFER по rate, затем все CASH по rate."""
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
            SourceCategory.CASH,
            _fake_summary([SourceQuote(1.0, "Cash")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, c, t])
        labels = [r.label for r in rows]
        self.assertEqual(labels[0], "Forex")
        i_cash = labels.index("Cash")
        i_t = labels.index("T")
        self.assertLess(i_t, i_cash)

    def test_source_quote_category_overrides_source(self):
        """Котировка может задать CASH, не меняя категорию плагина (TRANSFER)."""
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
                        category=SourceCategory.CASH,
                    ),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, hybrid])
        labels = [r.label for r in rows]
        self.assertLess(labels.index("Online"), labels.index("Cash desk"))
        cash_row = next(r for r in rows if r.label == "Cash desk")
        self.assertEqual(cash_row.category, SourceCategory.CASH)

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


if __name__ == "__main__":
    unittest.main()
