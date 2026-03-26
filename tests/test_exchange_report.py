# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import exchange_report as er


class TestExchangeReport(unittest.TestCase):
    @patch.object(er, "_ttexchange_api_module")
    def test_sorts_by_usd_desc(self, mock_tt_mod: object) -> None:
        api = MagicMock()
        api.get_stores.return_value = [
            {"branch_id": "1", "name": "Low Office"},
            {"branch_id": "2", "name": "High Office"},
        ]

        def _cur(branch_id: str, **kwargs):
            if branch_id == "1":
                return [
                    {"name": "USD(L)", "current_buy_rate": 31.0},
                    {"name": "EUR(L)", "current_buy_rate": 36.0},
                ]
            return [
                {"name": "USD(L)", "current_buy_rate": 35.0},
                {"name": "EUR(L)", "current_buy_rate": 38.0},
                {"name": "CNY(L)", "current_buy_rate": 4.5},
            ]

        api.get_currencies.side_effect = _cur
        mock_tt_mod.return_value = api

        text, w = er.build_exchange_report_text(
            top_n=10, lang="ru", timeout=5.0, refresh=True
        )
        self.assertFalse(w, w)
        lines = [ln for ln in text.strip().split("\n") if ln.strip()]
        body = "\n".join(lines)
        self.assertIn("High Office", body)
        self.assertIn("Low Office", body)
        high_i = body.index("High Office")
        low_i = body.index("Low Office")
        self.assertLess(high_i, low_i)
        self.assertNotIn("Ex24", body)

    @patch.object(er, "_ttexchange_api_module")
    def test_skips_branches_whose_name_contains_closed(self, mock_tt_mod: object) -> None:
        api = MagicMock()
        api.get_stores.return_value = [
            {"branch_id": "1", "name": "Open Office"},
            {"branch_id": "2", "name": "Best Office (Temporary Closed)"},
        ]

        def _cur(branch_id: str, **kwargs):
            return [{"name": "USD(L)", "current_buy_rate": 31.0}]

        api.get_currencies.side_effect = _cur
        mock_tt_mod.return_value = api

        text, _w = er.build_exchange_report_text(
            top_n=10, lang="ru", timeout=5.0, refresh=True
        )
        self.assertIn("Open Office", text)
        self.assertNotIn("Temporary Closed", text)
        self.assertNotIn("99.00", text)
        api.get_currencies.assert_called_once()


if __name__ == "__main__":
    unittest.main()
