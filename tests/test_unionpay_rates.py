# -*- coding: utf-8 -*-
"""Тесты без импорта пакета sources.rshb_unionpay (циклический импорт с rates_sources)."""
from __future__ import annotations

import importlib.util
import sys
import unittest
import urllib.error
from datetime import date
from pathlib import Path
from unittest.mock import patch

_ROOT = Path(__file__).resolve().parent.parent


def _load_unionpay_rates_module():
    path = _ROOT / "sources" / "rshb_unionpay" / "unionpay_rates.py"
    spec = importlib.util.spec_from_file_location("_unionpay_rates_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    spec.loader.exec_module(mod)
    return mod


class TestUnionpay404Fallback(unittest.TestCase):
    def test_fetch_daily_file_none_falls_back_on_404(self):
        ur = _load_unionpay_rates_module()
        calls: list[str] = []

        def fake_get_json(url: str, *, timeout: float = 45.0):
            calls.append(url)
            if "20260403.json" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
            if "20260402.json" in url:
                return {"exchangeRateJson": []}
            raise AssertionError(url)

        with patch.object(ur, "_get_json", side_effect=fake_get_json):
            with patch.object(ur, "date") as m_date:
                m_date.today.return_value = date(2026, 4, 3)
                m_date.side_effect = date
                got = ur.fetch_daily_file(None, timeout=1.0)
        self.assertEqual(got, {"exchangeRateJson": []})
        self.assertTrue(any("20260403" in u for u in calls))
        self.assertTrue(any("20260402" in u for u in calls))

    def test_explicit_date_no_fallback(self):
        ur = _load_unionpay_rates_module()
        with patch.object(ur, "_get_json") as m:
            m.side_effect = urllib.error.HTTPError("url", 404, "N", hdrs=None, fp=None)
            with self.assertRaises(urllib.error.HTTPError):
                ur.fetch_daily_file(date(2026, 4, 3), timeout=1.0)


if __name__ == "__main__":
    unittest.main()
