# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

from rates_p2p_rub_summary import (
    SUMMARY_P2P_FALLBACK_NOTE,
    WARN_HTX_NO_CASH,
    WARN_HTX_NO_TRANSFER,
    SummaryP2pRubLegs,
    _build_summary_legs,
    min_rub_per_usdt,
    read_summary_p2p_rub_legs,
)


class TestP2pRubHtxFallback(unittest.TestCase):
    def test_build_uses_htx_when_bybit_empty(self) -> None:
        legs = _build_summary_legs(None, None, 80.0, 79.0, tried_htx=True)
        self.assertEqual(legs.cash_rub_per_usdt, 80.0)
        self.assertEqual(legs.transfer_rub_per_usdt, 79.0)
        self.assertTrue(legs.cash_via_htx)
        self.assertTrue(legs.transfer_via_htx)
        self.assertIn(SUMMARY_P2P_FALLBACK_NOTE, legs.warnings)

    def test_build_keeps_bybit_when_present(self) -> None:
        legs = _build_summary_legs(81.0, 80.0, 70.0, 69.0, tried_htx=False)
        self.assertEqual(legs.cash_rub_per_usdt, 81.0)
        self.assertFalse(legs.cash_via_htx)
        self.assertNotIn(SUMMARY_P2P_FALLBACK_NOTE, legs.warnings)

    def test_build_partial_htx(self) -> None:
        legs = _build_summary_legs(81.0, None, None, 79.5, tried_htx=True)
        self.assertEqual(legs.cash_rub_per_usdt, 81.0)
        self.assertEqual(legs.transfer_rub_per_usdt, 79.5)
        self.assertFalse(legs.cash_via_htx)
        self.assertTrue(legs.transfer_via_htx)
        self.assertIn(SUMMARY_P2P_FALLBACK_NOTE, legs.warnings)

    def test_min_rub_per_usdt_picks_htx_flag(self) -> None:
        legs = SummaryP2pRubLegs(81.0, 79.0, cash_via_htx=False, transfer_via_htx=True)
        p, via = min_rub_per_usdt(legs)
        self.assertEqual(p, 79.0)
        self.assertTrue(via)

    def test_read_from_unified_doc_mock(self) -> None:
        now = time.time()
        doc = {
            "prim": {
                "prim:bybit:p2p_rub_usdt:v1": {
                    "saved_unix": now,
                    "ttl_sec": 3600,
                    "version": 1,
                    "payload": {"cash_price": None, "transfer_price": None, "warnings": ["x"]},
                },
                "prim:htx:p2p_rub_usdt:v1": {
                    "saved_unix": now,
                    "ttl_sec": 3600,
                    "version": 1,
                    "payload": {"cash_price": 88.0, "transfer_price": None, "warnings": []},
                },
            }
        }
        legs = read_summary_p2p_rub_legs(doc)
        self.assertEqual(legs.cash_rub_per_usdt, 88.0)
        self.assertIn(SUMMARY_P2P_FALLBACK_NOTE, legs.warnings)
        if legs.transfer_rub_per_usdt is None:
            self.assertIn(WARN_HTX_NO_TRANSFER, legs.warnings)
