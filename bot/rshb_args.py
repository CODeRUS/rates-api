# -*- coding: utf-8 -*-
from __future__ import annotations


def parse_rshb_command_args(text: str) -> tuple[float, float]:
    """
    /rshb [THB] [ATM_FEE] -> (thb, atm_fee).
    По умолчанию: 30000 и 250.
    """
    msg = (text or "").strip()
    tokens = msg.split()
    thb = 30_000.0
    atm_fee = 250.0
    if len(tokens) > 1:
        thb = float(tokens[1])
    if len(tokens) > 2:
        atm_fee = float(tokens[2])
    if len(tokens) > 3:
        raise ValueError("too many args")
    if thb <= 0:
        raise ValueError("thb must be > 0")
    if atm_fee <= 0:
        raise ValueError("atm_fee must be > 0")
    return thb, atm_fee
