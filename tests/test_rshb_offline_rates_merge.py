# -*- coding: utf-8 -*-
"""Парсер rates_offline/rates_online: несколько блоков на одну дату."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "rshb_offline_rates_testiso",
    _ROOT / "sources" / "rshb_unionpay" / "rshb_offline_rates.py",
)
off = importlib.util.module_from_spec(_spec)
sys.modules["rshb_offline_rates_testiso"] = off
assert _spec.loader is not None
_spec.loader.exec_module(off)


class TestRshbMergeDuplicateDates(unittest.TestCase):
    def test_first_policy_merges_pairs_from_later_same_day_snapshot(self) -> None:
        html = """
        <strong>26.03.2026</strong>
        <table>
        <tr><td>USD/CNY</td><td>6.0</td><td>7.0</td></tr>
        <tr><td>EUR/USD</td><td>1.0</td><td>2.0</td></tr>
        </table>
        <strong>26.03.2026</strong>
        <table>
        <tr><td>CNY/RUR</td><td>11.0</td><td>12.5</td></tr>
        <tr><td>USD/RUR</td><td>80.0</td><td>85.0</td></tr>
        </table>
        """
        tables = off.parse_offline_html(html, duplicate_date_policy="first")
        d = date(2026, 3, 26)
        self.assertIn(d, tables)
        sell = off.cny_rur_sell(on=d, html=html)
        self.assertEqual(sell, off.Decimal("12.5"))


if __name__ == "__main__":
    unittest.main()
