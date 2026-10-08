# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest import mock

from rates_sources import FetchContext, SourceCategory
from sources.xaacta import parse_rub_per_thb, summary, thb_per_usdt_from_calc


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


class TestXaactaSource(unittest.TestCase):
    def test_parse_rub_per_thb(self) -> None:
        rate = parse_rub_per_thb(
            {
                "amountFrom": 82367.16,
                "amountTo": 30000.0,
                "rate": 0.36422277,
            }
        )
        self.assertAlmostEqual(rate, 82367.16 / 30000.0)

    def test_parse_thb_per_usdt(self) -> None:
        self.assertAlmostEqual(
            thb_per_usdt_from_calc({"amountFrom": 1000.0, "amountTo": 32727.8}),
            32.7278,
        )

    def test_parse_rejects_bad_payload(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_rub_per_thb({"rate": 0.36})
        with self.assertRaises(RuntimeError):
            parse_rub_per_thb({"amountFrom": 0, "amountTo": 10})

    @mock.patch("sources.xaacta.fetch_calc")
    def test_summary_uses_receiving_thb(self, m_fetch) -> None:
        m_fetch.return_value = {"amountFrom": 82367.16, "amountTo": 30000.0}
        ctx = _ctx(receiving_thb=30_000.0)
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].label, "XAACTA")
        self.assertEqual(rows[0].category, SourceCategory.EXCHANGER)
        self.assertEqual(rows[0].note, "≈ 30 000 THB")
        self.assertAlmostEqual(rows[0].rate, 82367.16 / 30000.0)
        m_fetch.assert_called_once_with(30_000.0, "TO")
        self.assertEqual(ctx.warnings, [])

    @mock.patch("sources.xaacta.fetch_calc", side_effect=RuntimeError("HTTP 400"))
    def test_summary_warning_on_error(self, _fetch) -> None:
        ctx = _ctx()
        self.assertIsNone(summary(ctx))
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("HTTP 400", ctx.warnings[0])


if __name__ == "__main__":
    unittest.main()
