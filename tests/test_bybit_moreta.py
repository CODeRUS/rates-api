# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
import unittest.mock
from typing import Optional

from rates_primitives import PRIM_BYBIT_P2P_RUB, PRIM_MORETA_EXCHANGE_RATES
from rates_sources import FetchContext
import rates_unified_cache as ucc

import sources.bybit_moreta as bm


class TestBybitMoretaSummary(unittest.TestCase):
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

    def _doc(self, *, cash_p: float, tr_p: float, thb: float) -> dict:
        doc = ucc._empty_doc()
        ucc.prim_set(
            doc,
            PRIM_BYBIT_P2P_RUB,
            {"cash_price": cash_p, "transfer_price": tr_p, "warnings": []},
            ttl_sec=3600,
        )
        ucc.prim_set(
            doc,
            PRIM_MORETA_EXCHANGE_RATES,
            {"thb_per_usdt": thb, "warnings": []},
            ttl_sec=3600,
        )
        return doc

    def test_from_primitives(self) -> None:
        doc = self._doc(cash_p=100, tr_p=88, thb=32.0)
        ctx = self._ctx(doc)
        out = bm.summary(ctx)
        assert out is not None
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].label, bm._LABEL)
        usdt_20k = 20_000.0 / 32.0 + bm._TRANSFER_FEE_USD
        self.assertAlmostEqual(out[0].rate, usdt_20k * 88.0 / 20_000.0, places=6)


class TestMoretaApiParse(unittest.TestCase):
    def test_usd_thb(self) -> None:
        from sources.bybit_moreta import moreta_api as ma

        payload = {"rates": {"USD_THB": 32.41, "USD_PHP": 60}, "lastUpdated": 1}
        with unittest.mock.patch.object(ma, "_get_json", return_value=payload):
            v, w = ma.fetch_thb_per_usdt()
        self.assertEqual(w, "")
        self.assertAlmostEqual(v or 0, 32.41)


if __name__ == "__main__":
    unittest.main()
