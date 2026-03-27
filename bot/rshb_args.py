# -*- coding: utf-8 -*-
from __future__ import annotations


def parse_rshb_command_args(text: str) -> tuple[list[float], float]:
    """
    /rshb [THB …] [ATM_FEE] -> (список сумм снятия, комиссия ATM).

    Два числа после команды: THB и fee; три и больше: суммы подряд, последнее — fee.
    По умолчанию: одна сумма 30000 и fee 250.
    """
    msg = (text or "").strip()
    tokens = msg.split()
    if len(tokens) <= 1:
        return [30_000.0], 250.0
    raw = tokens[1:]
    nums: list[float] = []
    for t in raw:
        try:
            nums.append(float(t))
        except ValueError:
            raise ValueError("non-numeric rshb arg")
    n = len(nums)
    if n == 1:
        if nums[0] <= 0:
            raise ValueError("thb must be > 0")
        return [nums[0]], 250.0
    if n == 2:
        if nums[0] <= 0:
            raise ValueError("thb must be > 0")
        if nums[1] <= 0:
            raise ValueError("atm_fee must be > 0")
        return [nums[0]], nums[1]
    amounts, fee = nums[:-1], nums[-1]
    for x in amounts:
        if x <= 0:
            raise ValueError("thb must be > 0")
    if fee <= 0:
        raise ValueError("atm_fee must be > 0")
    return amounts, fee
