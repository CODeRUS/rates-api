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


if __name__ == "__main__":
    unittest.main()
