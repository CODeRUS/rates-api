# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest import mock

from rates_sources import FetchContext, SourceCategory
from sources.bereza import _extract_to_amount, summary


def _ctx() -> FetchContext:
    return FetchContext(
        thb_ref=2.5,
        atm_fee=220.0,
        korona_small_rub=100000.0,
        korona_large_thb=70000.0,
        avosend_rub=100000.0,
        unionpay_date=None,
        moex_override=None,
        warnings=[],
    )


class TestBerezaSource(unittest.TestCase):
    def test_extract_to_amount_candidates(self) -> None:
        self.assertEqual(_extract_to_amount({"to_amount": 123.4}), 123.4)
        self.assertEqual(_extract_to_amount({"result": "456.7"}), 456.7)
        self.assertEqual(_extract_to_amount({"data": {"converted_amount": 11}}), 11.0)

    @mock.patch("sources.bereza._convert_rub_to_thb")
    def test_summary_returns_transfer_and_cash(self, m_convert) -> None:
        m_convert.side_effect = [2.51, 2.63]
        ctx = _ctx()
        rows = summary(ctx)
        self.assertIsNotNone(rows)
        assert rows is not None
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].label, "Bereza Transfer")
        self.assertEqual(rows[0].category, SourceCategory.TRANSFER)
        self.assertEqual(rows[1].label, "Bereza Cash")
        self.assertEqual(rows[1].category, SourceCategory.CASH_RUB)
        self.assertEqual(ctx.warnings, [])


if __name__ == "__main__":
    unittest.main()
