# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from decimal import Decimal
from typing import Optional
from unittest import mock

import rates_unified_cache as ucc
from rates_p2p_rub_summary import SummaryP2pRubLegs
from rates_primitives import (
    PRIM_BYBIT_P2P_RUB,
    PRIM_BYBIT_USDC_USDT,
    PRIM_HTX_P2P_RUB,
    PRIM_TRUEMONEY_THBT,
)
from rates_sources import FetchContext

import sources.bybit_truemoney as tm
from sources.bybit_truemoney.bahtswap_quote import (
    CHAIN_ID,
    EXACT_RAW,
    HOOK,
    THBT,
    USDC,
    USDT,
    decode_amount_out,
    encode_quote_calldata,
    fetch_thbt_quote_payload,
    parse_quote_batch,
    thbt_per_token,
)
from sources.bybit_truemoney.bybit_usdc_usdt import ask_from_ticker_payload

_USDT_OUT = 33844833
_USDC_OUT = 33447281
_ASK = "0.9997"


def _word(raw: bytes, index: int) -> bytes:
    start = index * 32
    return raw[start : start + 32]


def _ctx(doc: Optional[dict] = None) -> FetchContext:
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


def _doc(
    *,
    cash: Optional[float],
    transfer: Optional[float],
    usdt_out: Optional[int],
    usdc_out: Optional[int],
    ask: Optional[str],
    htx_cash: Optional[float] = None,
    htx_transfer: Optional[float] = None,
    quote_warnings: Optional[list] = None,
    ask_warnings: Optional[list] = None,
) -> dict:
    doc = ucc._empty_doc()
    ucc.prim_set(
        doc,
        PRIM_BYBIT_P2P_RUB,
        {"cash_price": cash, "transfer_price": transfer, "warnings": []},
        ttl_sec=3600,
    )
    ucc.prim_set(
        doc,
        PRIM_HTX_P2P_RUB,
        {"cash_price": htx_cash, "transfer_price": htx_transfer, "warnings": []},
        ttl_sec=3600,
    )
    ucc.prim_set(
        doc,
        PRIM_TRUEMONEY_THBT,
        {
            "usdt_amount_out": usdt_out,
            "usdc_amount_out": usdc_out,
            "warnings": list(quote_warnings or []),
        },
        ttl_sec=3600,
    )
    ucc.prim_set(
        doc,
        PRIM_BYBIT_USDC_USDT,
        {"ask_usdt_per_usdc": ask, "warnings": list(ask_warnings or [])},
        ttl_sec=3600,
    )
    return doc


class TestBahtSwapCalldata(unittest.TestCase):
    def _body(self, token: str) -> bytes:
        raw = bytes.fromhex(encode_quote_calldata(token)[2:])
        self.assertEqual(raw[:4].hex(), "aa9d21cb")
        self.assertEqual(int.from_bytes(raw[4:36], "big"), 0x20)
        self.assertEqual(len(raw), 4 + 32 + 0x140)
        return raw[36:]

    def _assert_common(self, body: bytes, *, currency0: str, currency1: str, zero_for_one: int) -> None:
        self.assertEqual(_word(body, 0)[-20:].hex(), currency0[2:].lower())
        self.assertEqual(_word(body, 1)[-20:].hex(), currency1[2:].lower())
        self.assertEqual(int.from_bytes(_word(body, 2), "big"), 0)
        self.assertEqual(int.from_bytes(_word(body, 3), "big"), 1)
        self.assertEqual(_word(body, 4)[-20:].hex(), HOOK[2:].lower())
        self.assertEqual(int.from_bytes(_word(body, 5), "big"), zero_for_one)
        self.assertEqual(int.from_bytes(_word(body, 6), "big"), EXACT_RAW)
        self.assertEqual(int.from_bytes(_word(body, 7), "big"), 0x100)
        tail = body[0x100:]
        self.assertEqual(int.from_bytes(tail[:32], "big"), 20)
        self.assertEqual(tail[32:52].hex(), HOOK[2:].lower())
        self.assertEqual(tail[52:64], b"\x00" * 12)
        self.assertNotEqual(tail[32:64], b"\x00" * 12 + bytes.fromhex(HOOK[2:]))

    def test_usdt_direction(self) -> None:
        self._assert_common(
            self._body(USDT),
            currency0=THBT,
            currency1=USDT,
            zero_for_one=0,
        )

    def test_usdc_direction(self) -> None:
        self._assert_common(
            self._body(USDC),
            currency0=USDC,
            currency1=THBT,
            zero_for_one=1,
        )

    def test_decode_documented_amounts(self) -> None:
        self.assertEqual(thbt_per_token(_USDT_OUT), Decimal("33.844833"))
        self.assertEqual(thbt_per_token(_USDC_OUT), Decimal("33.447281"))
        result = "0x" + f"{_USDT_OUT:064x}" + f"{49487:064x}"
        self.assertEqual(decode_amount_out(result), _USDT_OUT)
        self.assertIsNone(decode_amount_out("0x"))
        self.assertIsNone(decode_amount_out("0x" + "00" * 32))

    def test_batch_keeps_usdc_when_usdt_reverts(self) -> None:
        usdc_result = "0x" + f"{_USDC_OUT:064x}" + "00" * 32
        parsed = parse_quote_batch(
            [
                {"id": 1, "result": hex(CHAIN_ID)},
                {"id": 2, "error": {"code": 3, "message": "execution reverted"}},
                {"id": 3, "result": usdc_result},
            ]
        )
        self.assertIsNone(parsed["usdt_amount_out"])
        self.assertEqual(parsed["usdc_amount_out"], _USDC_OUT)
        self.assertTrue(any("USDT→THBT" in w for w in parsed["warnings"]))

    def test_wrong_chain_discards_quotes(self) -> None:
        parsed = parse_quote_batch(
            [
                {"id": 1, "result": "0x1"},
                {"id": 2, "result": "0x" + f"{_USDT_OUT:064x}" + "00" * 32},
                {"id": 3, "result": "0x" + f"{_USDC_OUT:064x}" + "00" * 32},
            ]
        )
        self.assertIsNone(parsed["usdt_amount_out"])
        self.assertIsNone(parsed["usdc_amount_out"])
        self.assertIn("chainId", parsed["warnings"][0])

    def test_fetch_posts_batch_once(self) -> None:
        usdc_result = "0x" + f"{_USDC_OUT:064x}" + f"{50047:064x}"
        with mock.patch(
            "sources.bybit_truemoney.bahtswap_quote._post_rpc",
            return_value=[
                {"id": 1, "result": hex(CHAIN_ID)},
                {"id": 2, "error": {"code": 3, "message": "execution reverted"}},
                {"id": 3, "result": usdc_result},
            ],
        ) as post:
            payload = fetch_thbt_quote_payload()
        self.assertEqual(post.call_count, 1)
        sent = post.call_args.args[0]
        self.assertEqual([item["method"] for item in sent], ["eth_chainId", "eth_call", "eth_call"])
        self.assertEqual(sent[1]["params"][0]["data"], encode_quote_calldata(USDT))
        self.assertEqual(sent[2]["params"][0]["data"], encode_quote_calldata(USDC))
        self.assertIsNone(payload["usdt_amount_out"])
        self.assertEqual(payload["usdc_amount_out"], _USDC_OUT)


class TestBybitUsdcUsdtAsk(unittest.TestCase):
    def test_ask_from_ticker(self) -> None:
        ask = ask_from_ticker_payload(
            {
                "retCode": 0,
                "result": {"list": [{"symbol": "USDCUSDT", "ask1Price": "0.9997", "bid1Price": "0.9996"}]},
            }
        )
        self.assertEqual(ask, Decimal("0.9997"))

    def test_rejects_bad_retcode(self) -> None:
        with self.assertRaises(RuntimeError):
            ask_from_ticker_payload({"retCode": 10001, "result": {"list": []}})


class TestBybitTrueMoneySummary(unittest.TestCase):
    def test_formulas(self) -> None:
        doc = _doc(cash=90.0, transfer=88.0, usdt_out=_USDT_OUT, usdc_out=_USDC_OUT, ask=_ASK)
        ctx = _ctx(doc)
        out = tm.summary(ctx)
        assert out is not None
        by_label = {q.label: q for q in out}
        thbt_usdt = Decimal(_USDT_OUT) / Decimal(EXACT_RAW)
        thbt_usdc = Decimal(_USDC_OUT) / Decimal(EXACT_RAW)
        ask = Decimal(_ASK)
        self.assertEqual(
            set(by_label),
            {
                "Bybit P2P (cash) → TrueMoney USDT",
                "Bybit P2P (перевод) → TrueMoney USDT",
                "Bybit P2P (cash) → TrueMoney USDC",
                "Bybit P2P (перевод) → TrueMoney USDC",
            },
        )
        self.assertAlmostEqual(by_label["Bybit P2P (cash) → TrueMoney USDT"].rate, float(Decimal(90) / thbt_usdt), places=8)
        self.assertAlmostEqual(
            by_label["Bybit P2P (перевод) → TrueMoney USDT"].rate,
            float(Decimal(88) / thbt_usdt),
            places=8,
        )
        self.assertAlmostEqual(
            by_label["Bybit P2P (cash) → TrueMoney USDC"].rate,
            float(Decimal(90) * ask / thbt_usdc),
            places=8,
        )
        self.assertAlmostEqual(
            by_label["Bybit P2P (перевод) → TrueMoney USDC"].rate,
            float(Decimal(88) * ask / thbt_usdc),
            places=8,
        )
        self.assertFalse(ctx.warnings)
        for quote in out:
            self.assertIsNone(quote.merge_key)

    def test_usdc_missing_keeps_usdt_rows(self) -> None:
        doc = _doc(
            cash=90.0,
            transfer=88.0,
            usdt_out=_USDT_OUT,
            usdc_out=None,
            ask=_ASK,
            quote_warnings=["TrueMoney THBT: пустой ответ USDC→THBT"],
        )
        ctx = _ctx(doc)
        out = tm.summary(ctx)
        assert out is not None
        self.assertEqual(
            [q.label for q in out],
            [
                "Bybit P2P (cash) → TrueMoney USDT",
                "Bybit P2P (перевод) → TrueMoney USDT",
            ],
        )
        self.assertEqual(ctx.warnings, ["TrueMoney THBT: пустой ответ USDC→THBT"])

    def test_missing_ask_drops_usdc_rows(self) -> None:
        doc = _doc(
            cash=90.0,
            transfer=None,
            usdt_out=_USDT_OUT,
            usdc_out=_USDC_OUT,
            ask=None,
            ask_warnings=["Bybit USDCUSDT: нет ask1Price"],
        )
        ctx = _ctx(doc)
        out = tm.summary(ctx)
        assert out is not None
        self.assertEqual([q.label for q in out], ["Bybit P2P (cash) → TrueMoney USDT"])
        self.assertEqual(ctx.warnings, ["Bybit USDCUSDT: нет ask1Price"])

    def test_htx_cash_fallback_label(self) -> None:
        doc = _doc(
            cash=None,
            transfer=88.0,
            usdt_out=_USDT_OUT,
            usdc_out=_USDC_OUT,
            ask=_ASK,
            htx_cash=91.0,
        )
        ctx = _ctx(doc)
        out = tm.summary(ctx)
        assert out is not None
        labels = {q.label for q in out}
        self.assertIn("HTX P2P (наличные) → TrueMoney USDT", labels)
        self.assertIn("HTX P2P (наличные) → TrueMoney USDC", labels)
        self.assertIn("Bybit P2P (перевод) → TrueMoney USDT", labels)
        self.assertNotIn("Bybit P2P (cash) → TrueMoney USDT", labels)

    def test_direct_formula_helper(self) -> None:
        legs = SummaryP2pRubLegs(cash_rub_per_usdt=90.0, transfer_rub_per_usdt=None)
        out = tm.quotes_from_legs(
            legs,
            usdt_amount_out=_USDT_OUT,
            usdc_amount_out=_USDC_OUT,
            ask_usdt_per_usdc=_ASK,
        )
        assert out is not None
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
