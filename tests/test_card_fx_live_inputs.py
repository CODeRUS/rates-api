# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from sources.rshb_unionpay import card_fx_calculator as cfx


class TestCardFxPartialCache(unittest.TestCase):
    def test_partial_stale_warning_lists_source(self) -> None:
        msg = cfx.format_live_inputs_stale_warning((cfx._STALE_RSHB_OFFLINE,))
        self.assertIn("РСХБ offline", msg)
        self.assertIn("остальные источники", msg)

    def test_full_stale_warning(self) -> None:
        msg = cfx.format_live_inputs_stale_warning(
            (
                cfx._STALE_UNIONPAY,
                cfx._STALE_MOEX,
                cfx._STALE_RSHB_OFFLINE,
                cfx._STALE_RSHB_ONLINE,
            )
        )
        self.assertIn("последние сохранённые курсы", msg)

    @mock.patch("sources.rshb_unionpay.card_fx_calculator._save_live_inputs_cache")
    @mock.patch("sources.rshb_unionpay.card_fx_calculator.rshb_online_rates.fetch_rates_json")
    @mock.patch("sources.rshb_unionpay.card_fx_calculator.rshb_offline_rates.fetch_offline_page")
    @mock.patch("sources.rshb_unionpay.card_fx_calculator.moex_fx.cny_rub_tom")
    @mock.patch("sources.rshb_unionpay.card_fx_calculator.unionpay_rates.fetch_daily_file")
    def test_offline_timeout_uses_cache_for_offline_only(
        self,
        m_up,
        m_moex,
        m_off,
        m_on,
        _save,
    ) -> None:
        m_up.return_value = {"exchangeRateJson": []}
        with mock.patch.object(
            cfx.unionpay_rates,
            "cny_per_thb",
            return_value=0.21,
        ):
            m_moex.return_value = 10.6
            m_off.side_effect = TimeoutError("read timed out")
            m_on.return_value = "[]"
            with mock.patch.object(
                cfx.rshb_online_rates,
                "parse_rates_json",
                return_value={
                    date(2026, 5, 25): [
                        cfx.rshb_offline_rates.PairQuote("CNY/RUR", Decimal("1"), Decimal("10.9"))
                    ]
                },
            ):
                with mock.patch.object(
                    cfx.rshb_online_rates,
                    "cny_rur_sell",
                    return_value=Decimal("10.9"),
                ):
                    with tempfile.TemporaryDirectory() as td:
                        cache_path = Path(td) / ".card_fx_live_inputs_cache.json"
                        cache_path.write_text(
                            json.dumps(
                                {
                                    "cny_per_thb": 0.2,
                                    "moex_cny_rub": 10.0,
                                    "rshb_cny_rur_sell": "11.1",
                                    "rshb_table_date": "2026-05-23",
                                    "rshb_online_cny_rur_sell": "10.8",
                                    "rshb_online_table_date": "2026-05-24",
                                    "unionpay_payload": {"exchangeRateJson": []},
                                }
                            ),
                            encoding="utf-8",
                        )
                        with mock.patch.object(cfx, "LIVE_INPUTS_CACHE_FILE", cache_path):
                            result = cfx.fetch_live_inputs()
        self.assertEqual(result[8], (cfx._STALE_RSHB_OFFLINE,))
        self.assertAlmostEqual(result[0], 0.21)
        self.assertAlmostEqual(result[1], 10.6)
        self.assertEqual(result[2], Decimal("11.1"))


if __name__ == "__main__":
    unittest.main()
