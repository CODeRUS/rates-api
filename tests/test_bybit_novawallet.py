# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from typing import Optional
from unittest import mock

from rates_sources import FetchContext

import sources.bybit_novawallet as bn


class TestBybitNovaWalletSummary(unittest.TestCase):
    def _item(
        self,
        *,
        price: str,
        payments: list[str],
        rate: str = "99.5",
        qty: str = "500",
        min_amt: Optional[str] = None,
    ) -> dict:
        p = float(price)
        ma = min_amt if min_amt is not None else str(p * 100)
        return {
            "price": price,
            "payments": payments,
            "recentExecuteRate": rate,
            "lastQuantity": qty,
            "minAmount": ma,
        }

    @mock.patch.object(bn.bp, "filter_by_target_usdt", side_effect=lambda items, **kw: items)
    @mock.patch.object(bn.bp, "fetch_all_online_items")
    @mock.patch.object(bn, "fetch_cashout_fee_usd", return_value=(1.5, ""))
    @mock.patch.object(bn, "fetch_thb_per_usdt", return_value=(32.0, ""))
    def test_uses_min_price_across_cash_and_transfer(
        self, _thb, _fee, mock_fetch_items, _ft
    ) -> None:
        mock_fetch_items.return_value = [
            self._item(price="100", payments=["18"]),
            self._item(price="88", payments=["14"]),
        ]
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        out = bn.summary(ctx)
        assert out is not None
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].label, bn._LABEL_PLAIN)
        self.assertEqual(out[1].label, bn._LABEL_CASH_20K)
        # min(100, 88) = 88
        self.assertAlmostEqual(out[0].rate, 88.0 / 32.0, places=6)
        usd_20k = 20_000.0 / 32.0 + 1.5 + 10.0 / 32.0
        rub_cash = usd_20k * 88.0 / 20_000.0
        self.assertAlmostEqual(out[1].rate, rub_cash, places=6)

    @mock.patch.object(bn.bp, "filter_by_target_usdt", side_effect=lambda items, **kw: items)
    @mock.patch.object(bn.bp, "fetch_all_online_items")
    @mock.patch.object(bn, "fetch_cashout_fee_usd", return_value=(None, "ledger fail"))
    @mock.patch.object(bn, "fetch_thb_per_usdt", return_value=(32.0, ""))
    def test_fallback_cashout_fee_warning(
        self, _thb, _fee, mock_fetch_items, _ft
    ) -> None:
        mock_fetch_items.return_value = [self._item(price="90", payments=["18"])]
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        out = bn.summary(ctx)
        assert out is not None
        self.assertTrue(any("fallback" in w.lower() for w in ctx.warnings))
        usd_20k = 20_000.0 / 32.0 + bn._FALLBACK_CASHOUT_USD + 10.0 / 32.0
        self.assertAlmostEqual(out[1].rate, usd_20k * 90.0 / 20_000.0, places=6)

    @mock.patch.object(bn.bp, "filter_by_target_usdt", side_effect=lambda items, **kw: items)
    @mock.patch.object(bn.bp, "fetch_all_online_items", return_value=[])
    @mock.patch.object(bn, "fetch_cashout_fee_usd", return_value=(1.5, ""))
    @mock.patch.object(bn, "fetch_thb_per_usdt", return_value=(32.0, ""))
    def test_no_bybit_returns_none(self, _thb, _fee, _fi, _ft) -> None:
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        self.assertIsNone(bn.summary(ctx))
        self.assertTrue(ctx.warnings)


class TestNovaWalletApiParse(unittest.TestCase):
    def test_parse_rate_object(self) -> None:
        from sources.bybit_novawallet import novawallet_api as na

        with mock.patch.object(na, "_get_json", return_value={"currency": "THB", "rate": "31.5"}):
            v, w = na.fetch_thb_per_usdt()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 31.5)

    def test_parse_rate_list_thb(self) -> None:
        from sources.bybit_novawallet import novawallet_api as na

        with mock.patch.object(
            na,
            "_get_json",
            return_value=[
                {"currency": "EUR", "rate": "1"},
                {"currency": "THB", "rate": "33"},
            ],
        ):
            v, w = na.fetch_thb_per_usdt()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 33.0)

    def test_parse_ledger_fees_array(self) -> None:
        from sources.bybit_novawallet import novawallet_api as na

        payload = {
            "fees": [
                {"operation": "deposit", "usd": 0},
                {"operation": "cashout", "treshold": 0, "percent": 0, "usd": 1.5},
            ]
        }
        with mock.patch.object(na, "_get_json", return_value=payload):
            v, w = na.fetch_cashout_fee_usd()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 1.5)

    def test_parse_ledger_top_level_fee(self) -> None:
        from sources.bybit_novawallet import novawallet_api as na

        with mock.patch.object(
            na,
            "_get_json",
            return_value={"operation": "cashout", "usd": 2.0},
        ):
            v, w = na.fetch_cashout_fee_usd()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 2.0)


if __name__ == "__main__":
    unittest.main()
