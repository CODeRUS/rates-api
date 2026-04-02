# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from typing import Optional
from unittest import mock

from rates_sources import FetchContext
from rates_primitives import PRIM_BYBIT_P2P_RUB, PRIM_NOVAWALLET_LEDGER
import rates_unified_cache as ucc

import sources.bybit_novawallet as bn


class TestBybitNovaWalletSummary(unittest.TestCase):
    def _ctx(self, doc: Optional[dict]) -> FetchContext:
        return FetchContext(
            30_000,
            250,
            0,
            40_000,
            10_000,
            None,
            None,
            warnings=[],
            unified_doc=doc,
        )

    def _doc_with_prims(
        self,
        *,
        cash_p: float,
        tr_p: float,
        thb: float,
        fee: Optional[float],
    ) -> dict:
        doc = ucc._empty_doc()
        ucc.prim_set(
            doc,
            PRIM_BYBIT_P2P_RUB,
            {"cash_price": cash_p, "transfer_price": tr_p, "warnings": []},
            ttl_sec=3600,
        )
        ucc.prim_set(
            doc,
            PRIM_NOVAWALLET_LEDGER,
            {"thb_per_usdt": thb, "cashout_usd": fee, "warnings": []},
            ttl_sec=3600,
        )
        return doc

    def test_from_primitives_min_bybit_and_formulas(self) -> None:
        doc = self._doc_with_prims(cash_p=100, tr_p=88, thb=32.0, fee=1.5)
        ctx = self._ctx(doc)
        out = bn.summary(ctx)
        assert out is not None
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].label, bn._LABEL_PLAIN)
        self.assertEqual(out[1].label, bn._LABEL_CASH_20K)
        self.assertAlmostEqual(out[0].rate, 88.0 / 32.0, places=6)
        usd_20k = 20_000.0 / 32.0 + 1.5 + 10.0 / 32.0
        self.assertAlmostEqual(out[1].rate, usd_20k * 88.0 / 20_000.0, places=6)

    def test_fallback_cashout_fee_from_primitives(self) -> None:
        doc = self._doc_with_prims(cash_p=90, tr_p=90, thb=32.0, fee=None)
        ctx = self._ctx(doc)
        out = bn.summary(ctx)
        assert out is not None
        self.assertTrue(any("fallback" in w.lower() for w in ctx.warnings))
        usd_20k = 20_000.0 / 32.0 + bn._FALLBACK_CASHOUT_USD + 10.0 / 32.0
        self.assertAlmostEqual(out[1].rate, usd_20k * 90.0 / 20_000.0, places=6)

    @mock.patch.object(bn.bp, "fetch_all_online_items", return_value=[])
    def test_no_bybit_legacy_without_doc(self, _fi) -> None:
        ctx = self._ctx(None)
        self.assertIsNone(bn.summary(ctx))
        self.assertTrue(ctx.warnings)


class TestNovaWalletApiParse(unittest.TestCase):
    def test_parse_rate_object(self) -> None:
        from sources.bybit_novawallet import novawallet_api as na

        with mock.patch.object(na, "_get_json", return_value={"currency": "THB", "rate": "31.5"}):
            v, w = na.fetch_thb_per_usdt()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 31.5)

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


if __name__ == "__main__":
    unittest.main()
