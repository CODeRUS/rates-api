# -*- coding: utf-8 -*-
from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from unittest import mock

from rates_sources import (
    FetchContext,
    RateRow,
    RateSource,
    SourceCategory,
    SourceQuote,
    is_cash_category,
    run_sources,
)


def _fake_summary(rows):
    def f(ctx):
        return rows

    return f


class TestRatesSources(unittest.TestCase):
    def test_default_sources_order_and_baseline(self):
        rs = importlib.import_module("rates_sources")
        ids = [s.id for s in rs.DEFAULT_SOURCES]
        self.assertEqual(ids[0], "forex")
        self.assertTrue(rs.DEFAULT_SOURCES[0].is_baseline)
        self.assertEqual(sum(1 for s in rs.DEFAULT_SOURCES if s.is_baseline), 1)
        self.assertEqual(
            ids,
            [
                "forex",
                "rshb_unionpay",
                "bybit_bitkub",
                "bybit_novawallet",
                "bybit_moreta",
                "korona",
                "avosend",
                "multitransfer",
                "avosend_bkb",
                "ex24",
                "kwikpay",
                "askmoney",
                "payscan",
                "bereza",
                "ttexchange",
                "rbc_ttexchange",
                "tbank",
                "sberbank_qr",
                "unired_bkb",
                "userbot_cash",
            ],
        )
        cats = {s.id: s.category for s in rs.DEFAULT_SOURCES}
        self.assertEqual(cats["ttexchange"].name, "TRANSFER")
        for i in ids:
            self.assertEqual(cats[i].name, "TRANSFER")

    def test_run_sources_sort_and_dedup(self):
        base = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        a = RateSource(
            "a",
            "a",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "X"),
                    SourceQuote(2.0, "Y"),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, baseline, w = run_sources(ctx, [base, a])
        self.assertTrue(rows[0].is_baseline)
        self.assertEqual(
            [r.rate for r in rows[1:]],
            sorted([r.rate for r in rows[1:]]),
        )

    def test_run_sources_transfer_block_before_cash(self):
        """После baseline TRANSFER: все TRANSFER по rate, затем все наличные по категориям."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        t = RateSource(
            "t1",
            "t",
            False,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(3.0, "T")]),
        )
        c = RateSource(
            "cash",
            "c",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(1.0, "Cash")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        routes = [forex, c, t]
        rows, _, _ = run_sources(ctx, routes)
        labels = [r.label for r in rows]
        self.assertEqual(labels[0], "Forex")
        i_cash = labels.index("Cash")
        i_t = labels.index("T")
        self.assertLess(i_t, i_cash)

    def test_run_sources_cash_blocks_ordered_by_currency_then_rate(self):
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        fx = RateSource(
            "fx",
            "x",
            False,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.8, "X")]),
        )
        us_hi = RateSource(
            "us",
            "u",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(9.0, "UsdHi")]),
        )
        us_lo = RateSource(
            "us2",
            "u",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(8.0, "UsdLo")]),
        )
        rub = RateSource(
            "rub",
            "r",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(3.0, "Rub")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, fx, us_hi, us_lo, rub])
        labels = [r.label for r in rows]
        self.assertLess(labels.index("Rub"), labels.index("UsdHi"))
        self.assertLess(labels.index("UsdHi"), labels.index("UsdLo"))

    def test_cash_same_category_sorted_by_rate_ignores_source_list_order(self):
        """CASH_USD: по убыванию курса (THB/ед.); порядок не от порядка источников в списке."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        hi_first = RateSource(
            "vendor_high_first",
            "•",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(35.0, "Hi")]),
        )
        lo_second = RateSource(
            "vendor_low_second",
            "•",
            False,
            SourceCategory.CASH_USD,
            _fake_summary([SourceQuote(33.0, "Lo")]),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, hi_first, lo_second])
        cash_usd = [r for r in rows if r.category == SourceCategory.CASH_USD]
        self.assertEqual([r.label for r in cash_usd], ["Hi", "Lo"])
        self.assertEqual([r.rate for r in cash_usd], [35.0, 33.0])

    def test_source_quote_category_overrides_source(self):
        """Котировка может задать категорию наличных, не меняя категорию плагина (TRANSFER)."""
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        hybrid = RateSource(
            "hybrid",
            "🤑",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "Online"),
                    SourceQuote(
                        3.5,
                        "Cash desk",
                        category=SourceCategory.CASH_RUB,
                    ),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, hybrid])
        labels = [r.label for r in rows]
        self.assertLess(labels.index("Online"), labels.index("Cash desk"))
        cash_row = next(r for r in rows if r.label == "Cash desk")
        self.assertEqual(cash_row.category, SourceCategory.CASH_RUB)
        self.assertTrue(is_cash_category(cash_row.category))
        self.assertTrue(cash_row.compare_to_baseline)

    def test_source_quote_emoji_overrides_source(self):
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        src = RateSource(
            "x",
            "🤑",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(3.0, "A"),
                    SourceQuote(3.1, "B", emoji="•"),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, src])
        a = next(r for r in rows if r.label == "A")
        b = next(r for r in rows if r.label == "B")
        self.assertEqual(a.emoji, "🤑")
        self.assertEqual(b.emoji, "•")

    def test_cash_rub_sorted_by_rate_asc_rbc_pairs_mixed_in(self):
        forex = RateSource(
            "forex",
            "📈",
            True,
            SourceCategory.TRANSFER,
            _fake_summary([SourceQuote(2.5, "Forex")]),
        )
        low = RateSource(
            "a",
            "•",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(2.4, "Low", category=SourceCategory.CASH_RUB)]),
        )
        high = RateSource(
            "b",
            "•",
            False,
            SourceCategory.CASH_RUB,
            _fake_summary([SourceQuote(2.9, "High", category=SourceCategory.CASH_RUB)]),
        )
        rbc = RateSource(
            "rbc",
            "•",
            False,
            SourceCategory.TRANSFER,
            _fake_summary(
                [
                    SourceQuote(
                        2.6,
                        "M RBC",
                        category=SourceCategory.CASH_RUB,
                        cash_rub_seq=100,
                    ),
                ]
            ),
        )
        ctx = FetchContext(30_000, 250, 0, 40_000, 10_000, None, None)
        rows, _, _ = run_sources(ctx, [forex, low, high, rbc])
        cash_rub = [r for r in rows if r.category == SourceCategory.CASH_RUB]
        labels = [r.label for r in cash_rub]
        self.assertEqual(labels, ["Low", "M RBC", "High"])

    def test_parse_rbc_min_sell(self):
        from sources.rbc_cash_json import min_sell_rub_per_unit

        banks = [
            {"name": "A", "rate": {"sell": "82.0"}},
            {"name": "B", "rate": {"sell": "80.5"}},
            {"name": "C", "rate": {"buy": "79"}},
        ]
        v, nm = min_sell_rub_per_unit(banks)
        self.assertAlmostEqual(v, 80.5)
        self.assertEqual(nm, "B")

    def test_parse_ex24_cash_rub_buy_rub_per_thb(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_rub_buy_rub_per_thb

        frag = 'foo\\"RUB\\":{\\"buy\\":\\"0.5\\",\\"sell\\":\\"0.6\\"bar'
        self.assertAlmostEqual(parse_ex24_cash_rub_buy_rub_per_thb(frag), 2.0)

    def test_load_ex24_proxy_urls_file(self):
        from sources.ex24 import ex24_rub_thb

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write("# skip\n1.2.3.4:8080\n\nhttp://x:9\n")
            path = f.name
        try:
            os.environ["EX24_PROXIES_FILE"] = path
            urls = ex24_rub_thb.load_ex24_proxy_urls()
        finally:
            os.environ.pop("EX24_PROXIES_FILE", None)
            os.unlink(path)
        self.assertEqual(urls, ["http://1.2.3.4:8080", "http://x:9"])

    def test_rate_row_format_without_forex_delta_when_not_comparable(self):
        r = RateRow(
            33.2,
            "Desk",
            "•",
            note="THB/USD",
            category=SourceCategory.CASH_USD,
            compare_to_baseline=False,
        )
        line = r.format_line(2.5)
        self.assertNotIn("%", line)
        self.assertIn("33.200", line)
        self.assertIn("Desk", line)

    def test_parse_ex24_cash_eur_thb_per_eur(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        frag = 'x\\"EUR\\":{\\"buy\\":\\"40.0\\",\\"sell\\":\\"41\\"y'
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "EUR"), 40.0)

    def test_parse_ex24_cash_eur_thb_per_eur_fallback_denoms(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        # Симуляция случая, когда ex24 отдаёт деноминации вместо ровно "EUR".
        frag = (
            'tv\\":{\\"EUR 50\\":{\\"buy\\":\\"39.0\\",'
            '\\"EUR 100\\":{\\"buy\\":\\"40.0\\",'
            '\\"EUR 200\\":{\\"buy\\":\\"38.0\\"'
        )
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "EUR"), 40.0)

    def test_parse_ex24_cash_cny_thb_per_cny_fallback_denoms(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        frag = (
            'tv\\":{\\"CNY 50\\":{\\"buy\\":\\"4.50\\",'
            '\\"CNY 100\\":{\\"buy\\":\\"4.73\\",'
            '\\"CNY 200\\":{\\"buy\\":\\"4.40\\"'
        )
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "CNY"), 4.73)

    def test_ttexchange_eur_all_tiers_same_rate_omits_denoms(self):
        from sources.ttexchange import _pick_currency_row

        cur = [
            {"name": "EUR(L)", "current_buy_rate": 37.6, "description": "500"},
            {"name": "EUR(M)", "current_buy_rate": 37.6, "description": "200-100"},
            {"name": "EUR(S)", "current_buy_rate": 37.6, "description": "50-5"},
        ]
        row, tier_note, omit = _pick_currency_row(cur, "EUR")
        self.assertIsNotNone(row)
        self.assertEqual(float(row["current_buy_rate"]), 37.6)
        self.assertEqual(tier_note, "")
        self.assertTrue(omit)

    def test_ttexchange_usd_two_tiers_at_max_joins_when_rates_differ_across_tiers(self):
        from sources.ttexchange import _pick_currency_row

        cur = [
            {"name": "USD(L)", "current_buy_rate": 32.5, "description": "100-50"},
            {"name": "USD(M)", "current_buy_rate": 32.5, "description": "20-5"},
            {"name": "USD(S)", "current_buy_rate": 31.0, "description": "2-1"},
        ]
        row, tier_note, omit = _pick_currency_row(cur, "USD")
        self.assertFalse(omit)
        self.assertIn("100-50", tier_note)
        self.assertIn("20-5", tier_note)
        self.assertEqual(float(row["current_buy_rate"]), 32.5)

    def test_ttexchange_branch_label_normalizes(self):
        from sources.ttexchange import _branch_display_name, normalize_ttexchange_branch_label

        self.assertEqual(
            normalize_ttexchange_branch_label("NK2 : Naklua 2 Branch"),
            "Naklua 2",
        )
        self.assertEqual(normalize_ttexchange_branch_label("Naklua 2"), "Naklua 2")
        self.assertEqual(
            normalize_ttexchange_branch_label("HQ: Pattaya branch"),
            "Pattaya",
        )
        stores = [{"branch_id": "7", "name": "NK2 : Naklua 2 Branch"}]
        self.assertEqual(_branch_display_name(stores, "7"), "Naklua 2")

    def test_parse_tbank_atm_cashout_rub_per_thb(self):
        from sources.tbank import parse_atm_cashout_rub_per_thb

        payload = {
            "resultCode": "OK",
            "payload": {
                "rates": [
                    {
                        "category": "Other",
                        "fromCurrency": {"name": "RUB"},
                        "toCurrency": {"name": "THB"},
                        "buy": 99.0,
                    },
                    {
                        "category": "ATMCashoutRateGroup",
                        "fromCurrency": {"name": "RUB"},
                        "toCurrency": {"name": "THB"},
                        "buy": 0.4,
                    },
                ]
            },
        }
        self.assertAlmostEqual(parse_atm_cashout_rub_per_thb(payload), 2.5)

    def test_parse_ex24_cash_usd_max_buy_across_denoms(self):
        from sources.ex24.ex24_rub_thb import parse_ex24_cash_fiat_thb_per_fiat_unit

        frag = (
            'tv\\":{\\"USD 1-50\\":{\\"buy\\":\\"31.0\\",'
            '\\"USD 100\\":{\\"buy\\":\\"32.5\\",'
            '\\"USD 100 Old\\":{\\"buy\\":\\"32.0\\"'
        )
        self.assertAlmostEqual(parse_ex24_cash_fiat_thb_per_fiat_unit(frag, "USD"), 32.5)

    def test_htx_row_has_cash_by_id_and_name(self):
        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        self.assertTrue(
            hx.row_has_cash(
                {
                    "payMethods": [{"payMethodId": 169, "name": "Cash in Person"}],
                    "payMethod": "69",
                    "tradeCount": 200,
                }
            )
        )
        self.assertTrue(
            hx.row_has_cash(
                {"payMethods": [{"payMethodId": 70, "name": "Наличный расчёт"}], "payMethod": ""}
            )
        )
        self.assertFalse(
            hx.row_has_cash(
                {
                    "payMethods": [{"payMethodId": 69, "name": "SBP - Fast Bank Transfer"}],
                    "payMethod": "69",
                }
            )
        )

    def test_htx_partition_respects_target_usdt_and_rub_limits(self):
        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        rows = [
            {
                "price": "80",
                "tradeCount": 50,
                "minTradeLimit": "1",
                "maxTradeLimit": "1e9",
                "payMethods": [{"payMethodId": 169, "name": "Cash in Person"}],
            },
            {
                "price": "81",
                "tradeCount": 150,
                "minTradeLimit": "8100",
                "maxTradeLimit": "9000",
                "payMethods": [{"payMethodId": 169, "name": "Cash in Person"}],
            },
            {
                "price": "79",
                "tradeCount": 200,
                "minTradeLimit": "7900",
                "maxTradeLimit": "20000",
                "payMethods": [{"payMethodId": 69, "name": "SBP"}],
            },
        ]
        wc, wo = hx.partition_cash_non_cash(rows, target_usdt=100)
        self.assertEqual(len(wc), 1)
        self.assertEqual(float(wc[0]["price"]), 81.0)
        self.assertEqual(len(wo), 1)
        self.assertEqual(float(wo[0]["price"]), 79.0)

    def test_htx_rub_rejects_when_min_trade_limit_below_target_rub(self):
        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        row = {
            "price": "80",
            "tradeCount": 500,
            "minTradeLimit": "1000",
            "maxTradeLimit": "50000",
            "payMethods": [{"payMethodId": 69, "name": "SBP"}],
        }
        self.assertFalse(hx.row_rub_limits_allow_target_usdt(row, target_usdt=100))
        wc, wo = hx.partition_cash_non_cash([row], target_usdt=100)
        self.assertEqual(wc, [])
        self.assertEqual(wo, [])

    def test_htx_rub_accepts_min_equal_target_rub(self):
        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        row = {
            "price": "80",
            "tradeCount": 500,
            "minTradeLimit": "8000",
            "payMethods": [{"payMethodId": 69, "name": "SBP"}],
        }
        self.assertTrue(hx.row_rub_limits_allow_target_usdt(row, target_usdt=100))

    def test_htx_pay_method_field_only_ids(self):
        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        self.assertTrue(
            hx.row_has_cash({"payMethods": [], "payMethod": "69,21,28"})
        )

    def test_htx_fetch_best_one_page_when_both_buckets_found(self):
        from unittest.mock import patch

        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        pages: list[int] = []

        def fake_page(page: int, timeout: float = 60.0):
            pages.append(page)
            return {
                "code": 200,
                "totalPage": 30,
                "data": [
                    {
                        "price": "70",
                        "tradeCount": 200,
                        "minTradeLimit": "7000",
                        "maxTradeLimit": "1e9",
                        "payMethods": [{"payMethodId": 69, "name": "SBP"}],
                    },
                    {
                        "price": "75",
                        "tradeCount": 200,
                        "minTradeLimit": "7500",
                        "maxTradeLimit": "1e9",
                        "payMethods": [{"payMethodId": 169, "name": "Cash in Person"}],
                    },
                ],
            }

        with patch.object(hx, "fetch_trade_market_page", side_effect=fake_page):
            bc, bnc = hx.fetch_best_cash_and_non_cash_offers(max_pages=30, target_usdt=100)
        self.assertEqual(pages, [1])
        self.assertIsNotNone(bc)
        self.assertIsNotNone(bnc)
        self.assertEqual(float(bc["price"]), 75.0)
        self.assertEqual(float(bnc["price"]), 70.0)

    def test_htx_fetch_best_second_page_for_cash(self):
        from unittest.mock import patch

        from sources.htx_bitkub import htx_p2p_usdt_rub as hx

        pages: list[int] = []

        def fake_page(page: int, timeout: float = 60.0):
            pages.append(page)
            if page == 1:
                return {
                    "code": 200,
                    "totalPage": 5,
                    "data": [
                        {
                            "price": "70",
                            "tradeCount": 200,
                            "minTradeLimit": "7000",
                            "maxTradeLimit": "1e9",
                            "payMethods": [{"payMethodId": 69, "name": "SBP"}],
                        },
                    ],
                }
            if page == 2:
                return {
                    "code": 200,
                    "totalPage": 5,
                    "data": [
                        {
                            "price": "72",
                            "tradeCount": 200,
                            "minTradeLimit": "7200",
                            "maxTradeLimit": "1e9",
                            "payMethods": [{"payMethodId": 169, "name": "Cash in Person"}],
                        },
                    ],
                }
            return {"code": 200, "totalPage": 5, "data": []}

        with patch.object(hx, "fetch_trade_market_page", side_effect=fake_page):
            bc, bnc = hx.fetch_best_cash_and_non_cash_offers(max_pages=30, target_usdt=100)
        self.assertEqual(pages, [1, 2])
        self.assertIsNotNone(bc)
        self.assertIsNotNone(bnc)

    def test_bybit_fetch_best_one_page_when_both_scenarios_found(self):
        from unittest.mock import patch

        from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp

        posts: list[int] = []

        def fake_post(url: str, body: object, *, timeout: float = 60.0):
            posts.append(int(body["page"]))
            return {
                "ret_code": 0,
                "result": {
                    "count": 100,
                    "items": [
                        {
                            "price": "70",
                            "lastQuantity": "200",
                            "minAmount": "7000",
                            "payments": ["14"],
                            "recentExecuteRate": 99.0,
                        },
                        {
                            "price": "75",
                            "lastQuantity": "200",
                            "minAmount": "7500",
                            "payments": ["18"],
                            "recentExecuteRate": 99.0,
                        },
                    ],
                },
            }

        with patch.object(bp, "post_json", side_effect=fake_post):
            cash, bank = bp.fetch_best_cash_and_bank_transfer_items(
                size=20,
                target_usdt=100.0,
                min_completion=99.0,
            )
        self.assertEqual(posts, [1])
        self.assertIsNotNone(cash)
        self.assertIsNotNone(bank)
        self.assertEqual(float(cash["price"]), 75.0)
        self.assertEqual(float(bank["price"]), 70.0)

    def test_bybit_fetch_best_second_page_for_bank_only(self):
        from unittest.mock import patch

        from sources.bybit_bitkub import bybit_p2p_usdt_rub as bp

        posts: list[int] = []

        def fake_post(url: str, body: object, *, timeout: float = 60.0):
            page = int(body["page"])
            posts.append(page)
            if page == 1:
                return {
                    "ret_code": 0,
                    "result": {
                        "count": 50,
                        "items": [
                            {
                                "price": "75",
                                "lastQuantity": "200",
                                "minAmount": "7500",
                                "payments": ["18"],
                                "recentExecuteRate": 99.0,
                            },
                        ],
                    },
                }
            if page == 2:
                return {
                    "ret_code": 0,
                    "result": {
                        "count": 50,
                        "items": [
                            {
                                "price": "72",
                                "lastQuantity": "200",
                                "minAmount": "7200",
                                "payments": ["14"],
                                "recentExecuteRate": 99.0,
                            },
                        ],
                    },
                }
            return {"ret_code": 0, "result": {"count": 50, "items": []}}

        with patch.object(bp, "post_json", side_effect=fake_post):
            cash, bank = bp.fetch_best_cash_and_bank_transfer_items(
                size=20,
                target_usdt=100.0,
                min_completion=99.0,
            )
        self.assertEqual(posts, [1, 2])
        self.assertIsNotNone(cash)
        self.assertIsNotNone(bank)

    def test_bybit_item_passes_target_usdt_quantity_and_min_amount(self):
        from sources.bybit_bitkub.bybit_p2p_usdt_rub import item_passes_target_usdt_filters

        ok = {
            "price": "80",
            "minAmount": "9000",
            "lastQuantity": "500",
            "payments": ["18"],
            "recentExecuteRate": 99.0,
        }
        self.assertTrue(item_passes_target_usdt_filters(ok, target_usdt=100))

    def test_bybit_item_rejects_low_last_quantity(self):
        from sources.bybit_bitkub.bybit_p2p_usdt_rub import item_passes_target_usdt_filters

        bad = {
            "price": "80",
            "minAmount": "9000",
            "lastQuantity": "50",
            "payments": ["18"],
        }
        self.assertFalse(item_passes_target_usdt_filters(bad, target_usdt=100))

    def test_bybit_item_rejects_min_amount_below_target_rub(self):
        from sources.bybit_bitkub.bybit_p2p_usdt_rub import item_passes_target_usdt_filters

        bad = {
            "price": "80",
            "minAmount": "5000",
            "lastQuantity": "500",
            "payments": ["18"],
        }
        self.assertFalse(item_passes_target_usdt_filters(bad, target_usdt=100))

    def test_bybit_tradable_quantity_falls_back_to_quantity(self):
        from sources.bybit_bitkub.bybit_p2p_usdt_rub import item_passes_target_usdt_filters

        it = {"price": "80", "minAmount": "8000", "quantity": "200", "payments": []}
        self.assertTrue(item_passes_target_usdt_filters(it, target_usdt=100))

    def test_binance_th_fetch_bid_uses_bid_price(self):
        from sources.binance_th import usdt_thb_book as b

        fake = {"symbol": "USDTTHB", "bidPrice": "32.5200", "askPrice": "32.5300"}
        with mock.patch.object(b, "fetch_book_ticker", return_value=fake):
            self.assertAlmostEqual(b.fetch_bid_thb_per_usdt(timeout=1), 32.52)

    def test_binance_th_fetch_bid_rejects_zero(self):
        from sources.binance_th import usdt_thb_book as b

        with mock.patch.object(b, "fetch_book_ticker", return_value={"bidPrice": "0"}):
            with self.assertRaises(RuntimeError):
                b.fetch_bid_thb_per_usdt(timeout=1)

    def test_merge_bitkub_binanceth_when_rates_equal(self):
        import rates_sources as rs

        rows = [
            rs.RateRow(
                2.5,
                "Bybit P2P (наличные) → Bitkub",
                "💸",
                merge_key="bybit_cash",
            ),
            rs.RateRow(
                2.5,
                "Bybit P2P (наличные) → Binance TH",
                "💱",
                merge_key="bybit_cash",
            ),
        ]
        out = rs._merge_matching_bitkub_binanceth_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(
            out[0].label,
            "Bybit P2P (наличные) → Bitkub / Binance TH",
        )
        self.assertIsNone(out[0].merge_key)

    def test_merge_bitkub_binanceth_keeps_both_when_rates_differ(self):
        import rates_sources as rs

        rows = [
            rs.RateRow(2.5, "→ Bitkub", "💸", merge_key="bybit_cash"),
            rs.RateRow(2.6, "→ Binance TH", "💱", merge_key="bybit_cash"),
        ]
        out = rs._merge_matching_bitkub_binanceth_rows(rows)
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
