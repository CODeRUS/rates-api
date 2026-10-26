# -*- coding: utf-8 -*-
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from rates_primitives import PRIM_BITKUB_USDT_THB, PRIM_BYBIT_P2P_RUB
from rates_sources import FetchContext, fmt_money_ru
import rates_unified_cache as ucc

import sources.bybit_bitkub as bb


class TestBybitBitkubQuoteFormat(unittest.TestCase):
    def test_receiving_800_matches_displayed_rates(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=2.444 * 32.0,
            transfer_price=2.458 * 32.0,
            thb_per_usdt=32.0,
            receiving_thb=800,
        )
        self.assertIn("Bybit P2P → Bitkub, получить 800 THB", text)
        self.assertIn("Курс с лота 100 USDT.", text)
        self.assertIn("💸 1 955 RUB | 2.444 RUB/THB | cash", text)
        self.assertIn("💸 1 966 RUB | 2.458 RUB/THB | перевод", text)

    def test_leg_cash_only(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=2.444 * 32.0,
            transfer_price=2.458 * 32.0,
            thb_per_usdt=32.0,
            receiving_thb=800,
            leg="cash",
        )
        self.assertIn("💸 1 955 RUB | 2.444 RUB/THB | cash", text)
        self.assertNotIn("перевод", text)

    def test_sending_rub_to_thb(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=2.444 * 32.0,
            transfer_price=None,
            thb_per_usdt=32.0,
            sending_rub=2000,
            leg="cash",
        )
        self.assertIn(
            f"💸 {fmt_money_ru(2000 / 2.444)} THB | 2.444 RUB/THB | cash",
            text,
        )
        self.assertNotIn("RUB |", text)

    def test_rates_only_without_amount(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=80.0,
            transfer_price=81.0,
            thb_per_usdt=32.0,
        )
        self.assertTrue(text.startswith("Bybit P2P → Bitkub\n"))
        self.assertIn("💸 2.500 RUB/THB | cash", text)
        self.assertIn("💸 2.531 RUB/THB | перевод", text)
        self.assertNotIn(" RUB |", text)

    def test_missing_bid_has_warning_and_no_amount(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=80.0,
            transfer_price=81.0,
            thb_per_usdt=None,
            receiving_thb=800,
        )
        self.assertIn(bb.WARN_NO_BID, text)
        self.assertNotIn("RUB", text)

    def test_missing_cash_keeps_transfer_and_warning(self) -> None:
        text = bb.format_bybit_bitkub_quote(
            cash_price=None,
            transfer_price=2.458 * 32.0,
            thb_per_usdt=32.0,
            receiving_thb=800,
        )
        self.assertIn(bb.WARN_NO_CASH, text)
        self.assertIn("💸 1 966 RUB | 2.458 RUB/THB | перевод", text)
        self.assertNotIn("| cash", text)

    def test_summary_rate_stays_unrounded(self) -> None:
        doc = ucc._empty_doc()
        ucc.prim_set(
            doc,
            PRIM_BYBIT_P2P_RUB,
            {"cash_price": 80.0, "transfer_price": 81.0, "warnings": []},
            ttl_sec=3600,
        )
        ucc.prim_set(
            doc,
            PRIM_BITKUB_USDT_THB,
            {"highest_bid": 33.0, "warnings": []},
            ttl_sec=3600,
        )
        ctx = FetchContext(
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
        out = bb.summary(ctx)
        assert out is not None
        self.assertAlmostEqual(out[0].rate, 80.0 / 33.0, places=9)
        self.assertEqual(out[0].label, "Bybit P2P (cash) → Bitkub")
        self.assertAlmostEqual(out[1].rate, 81.0 / 33.0, places=9)


class TestBybitBitkubQuoteCommand(unittest.TestCase):
    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bb.quote_command(argv)
        return code, out.getvalue(), err.getvalue()

    def test_rejects_both_amounts(self) -> None:
        code, out, err = self._run(
            ["--receiving-thb", "800", "--sending-rub", "1000"]
        )
        self.assertEqual(code, 2)
        self.assertIn("не больше одного", err)
        self.assertNotIn("RUB |", out)

    @mock.patch.object(bb, "resolve_bybit_bitkub_prices", return_value=(None, None, None, []))
    def test_readonly_cache_miss_does_not_invent_amount(self, _resolve: mock.Mock) -> None:
        code, out, _err = self._run(["--receiving-thb", "800", "--readonly"])
        self.assertEqual(code, 0)
        self.assertIn(bb.WARN_NO_BID, out)
        self.assertNotIn("RUB |", out)
        _resolve.assert_called_once_with(allow_live=False)

    @mock.patch.object(
        bb,
        "resolve_bybit_bitkub_prices",
        return_value=(2.444 * 32.0, 2.458 * 32.0, 32.0, []),
    )
    def test_quote_prints_both_legs(self, _resolve: mock.Mock) -> None:
        code, out, _err = self._run(["--receiving-thb", "800"])
        self.assertEqual(code, 0)
        self.assertIn("1 955 RUB", out)
        self.assertIn("1 966 RUB", out)
        _resolve.assert_called_once_with(allow_live=True)


class TestBybitBitkubQuoteDispatch(unittest.TestCase):
    def test_main_forwards_receiving_thb_past_global_parser(self) -> None:
        import rates

        with mock.patch.object(bb, "command", return_value=0) as cmd:
            code = rates.main(
                ["bybit_bitkub", "quote", "--receiving-thb", "800", "--leg", "cash"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            cmd.call_args[0][0],
            ["quote", "--receiving-thb", "800", "--leg", "cash"],
        )

    def test_main_appends_readonly(self) -> None:
        import rates

        with mock.patch.object(bb, "command", return_value=0) as cmd:
            code = rates.main(
                ["--readonly", "bybit_bitkub", "quote", "--sending-rub", "2000"]
            )
        self.assertEqual(code, 0)
        self.assertEqual(
            cmd.call_args[0][0],
            ["quote", "--sending-rub", "2000", "--readonly"],
        )

    def test_resolve_uses_cache_and_skips_live(self) -> None:
        doc = ucc._empty_doc()
        ucc.prim_set(
            doc,
            PRIM_BYBIT_P2P_RUB,
            {"cash_price": 78.208, "transfer_price": 78.656, "warnings": []},
            ttl_sec=3600,
        )
        ucc.prim_set(
            doc,
            PRIM_BITKUB_USDT_THB,
            {"highest_bid": 32.0, "warnings": []},
            ttl_sec=3600,
        )
        with mock.patch.object(ucc, "load_unified", return_value=doc), mock.patch.object(
            bb, "_live_prices"
        ) as live:
            cash, transfer, thb, warns = bb.resolve_bybit_bitkub_prices(allow_live=True)
        live.assert_not_called()
        self.assertEqual(warns, [])
        self.assertEqual(thb, 32.0)
        self.assertEqual(cash, 78.208)
        self.assertEqual(transfer, 78.656)

    def test_resolve_live_when_cache_empty(self) -> None:
        doc = ucc._empty_doc()
        with mock.patch.object(ucc, "load_unified", return_value=doc), mock.patch.object(
            bb, "_live_prices", return_value=(80.0, None, 32.0)
        ) as live:
            cash, transfer, thb, _warns = bb.resolve_bybit_bitkub_prices(allow_live=True)
        live.assert_called_once_with()
        self.assertEqual((cash, transfer, thb), (80.0, None, 32.0))

    def test_resolve_readonly_skips_live(self) -> None:
        doc = ucc._empty_doc()
        with mock.patch.object(ucc, "load_unified", return_value=doc), mock.patch.object(
            bb, "_live_prices"
        ) as live:
            cash, transfer, thb, warns = bb.resolve_bybit_bitkub_prices(allow_live=False)
        live.assert_not_called()
        self.assertEqual((cash, transfer, thb, warns), (None, None, None, []))


if __name__ == "__main__":
    unittest.main()
