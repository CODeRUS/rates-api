# -*- coding: utf-8 -*-
"""USDT→THBT и USDC→THBT: eth_call quoteExactInputSingle на Base (Baht Swap / Uniswap v4)."""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any, Dict, List, Optional

from rates_http import urlopen_retriable

CHAIN_ID = 8453
QUOTER = "0x0d5e0f971ed27fbff6c2837bf31316121532048d"
HOOK = "0xF0930639457a2E64B4fA08fabE055b8Dc840Cac0"
THBT = "0xdC200537D99d8b4f0C89D59A68e29b67057d2c5F"
USDT = "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
DECIMALS = 6
EXACT_RAW = 10**DECIMALS
SELECTOR = bytes.fromhex("aa9d21cb")
DEFAULT_RPC = "https://mainnet.base.org"
_RPC_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
}

# Смещение hookData от начала struct: 8 головных слов по 32 байта.
_HOOK_DATA_OFFSET = 0x100


def rpc_url() -> str:
    raw = (os.environ.get("BASE_RPC_URL") or "").strip()
    return raw or DEFAULT_RPC


def _addr_bytes(addr: str) -> bytes:
    hex_part = addr[2:] if addr.lower().startswith("0x") else addr
    raw = bytes.fromhex(hex_part)
    if len(raw) != 20:
        raise ValueError(f"адрес не 20 байт: {addr}")
    return raw


def _word(n: int) -> bytes:
    if n < 0:
        raise ValueError("отрицательное слово ABI")
    return int(n).to_bytes(32, "big")


def _addr_word(addr: str) -> bytes:
    return b"\x00" * 12 + _addr_bytes(addr)


def encode_quote_calldata(token: str) -> str:
    """quoteExactInputSingle для exact input 1 токена в THBT.

    hookData — 20 сырых байт адреса hook, не ABI-address.
    zeroForOne = входной токен является currency0 (меньший адрес).
    """
    token_i = int(_addr_bytes(token).hex(), 16)
    thbt_i = int(_addr_bytes(THBT).hex(), 16)
    if token_i < thbt_i:
        currency0, currency1 = token, THBT
    else:
        currency0, currency1 = THBT, token
    zero_for_one = token_i == int(_addr_bytes(currency0).hex(), 16)
    hook = _addr_bytes(HOOK)
    struct = b"".join(
        (
            _addr_word(currency0),
            _addr_word(currency1),
            _word(0),
            _word(1),
            _addr_word(HOOK),
            _word(1 if zero_for_one else 0),
            _word(EXACT_RAW),
            _word(_HOOK_DATA_OFFSET),
            _word(len(hook)),
            hook + b"\x00" * (32 - len(hook)),
        )
    )
    return "0x" + (SELECTOR + _word(0x20) + struct).hex()


def decode_amount_out(result: Optional[str]) -> Optional[int]:
    """Первые 32 байта result — amountOut. Пустой result и 0 — нет курса."""
    if not isinstance(result, str):
        return None
    hex_part = result[2:] if result.lower().startswith("0x") else result
    if len(hex_part) < 64:
        return None
    try:
        amount = int(hex_part[:64], 16)
    except ValueError:
        return None
    if amount <= 0:
        return None
    return amount


def thbt_per_token(amount_out: int) -> Decimal:
    return Decimal(amount_out) / Decimal(EXACT_RAW)


def _post_rpc(payload: Any, *, timeout: float) -> Any:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url(),
        data=body,
        headers=_RPC_HEADERS,
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urlopen_retriable(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode(resp.headers.get_content_charset() or "utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} при eth_call Base") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("TrueMoney THBT: ответ RPC не JSON") from exc
    return data


def _rpc_error_text(item: Dict[str, Any]) -> str:
    err = item.get("error")
    if isinstance(err, dict):
        msg = err.get("message")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    return "error"


def parse_quote_batch(resp: Any) -> Dict[str, Any]:
    """Разбор batch: id 1 chainId, id 2 USDT→THBT, id 3 USDC→THBT."""
    empty: Dict[str, Any] = {
        "usdt_amount_out": None,
        "usdc_amount_out": None,
        "warnings": [],
    }
    if not isinstance(resp, list):
        empty["warnings"] = ["TrueMoney THBT: RPC batch не список"]
        return empty
    by_id: Dict[Any, Dict[str, Any]] = {}
    for item in resp:
        if isinstance(item, dict) and "id" in item:
            by_id[item["id"]] = item

    chain_item = by_id.get(1)
    if not isinstance(chain_item, dict) or "result" not in chain_item or "error" in chain_item:
        empty["warnings"] = ["TrueMoney THBT: нет chainId"]
        return empty
    try:
        chain_id = int(str(chain_item["result"]), 16)
    except (TypeError, ValueError):
        empty["warnings"] = ["TrueMoney THBT: неверный chainId"]
        return empty
    if chain_id != CHAIN_ID:
        empty["warnings"] = [f"TrueMoney THBT: chainId {chain_id}, ожидался {CHAIN_ID}"]
        return empty

    warnings: List[str] = []
    out: Dict[str, Any] = {}
    for rpc_id, key, label in (
        (2, "usdt_amount_out", "USDT→THBT"),
        (3, "usdc_amount_out", "USDC→THBT"),
    ):
        item = by_id.get(rpc_id)
        if not isinstance(item, dict):
            out[key] = None
            warnings.append(f"TrueMoney THBT: пустой ответ {label}")
            continue
        if "error" in item:
            out[key] = None
            warnings.append(f"TrueMoney THBT {label}: {_rpc_error_text(item)}")
            continue
        amount = decode_amount_out(item.get("result") if isinstance(item.get("result"), str) else None)
        if amount is None:
            out[key] = None
            warnings.append(f"TrueMoney THBT: пустой ответ {label}")
            continue
        out[key] = amount
    out["warnings"] = warnings
    return out


def fetch_thbt_quote_payload(*, timeout: float = 30.0) -> Dict[str, Any]:
    """Один JSON-RPC batch. Логическая ошибка quoter не повторяется."""
    try:
        resp = _post_rpc(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []},
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "eth_call",
                    "params": [{"to": QUOTER, "data": encode_quote_calldata(USDT)}, "latest"],
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "eth_call",
                    "params": [{"to": QUOTER, "data": encode_quote_calldata(USDC)}, "latest"],
                },
            ],
            timeout=timeout,
        )
    except RuntimeError as exc:
        return {
            "usdt_amount_out": None,
            "usdc_amount_out": None,
            "warnings": [f"TrueMoney THBT: {exc}"],
        }
    return parse_quote_batch(resp)
