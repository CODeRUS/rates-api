# -*- coding: utf-8 -*-
"""Разбор токенов команды /rates для бота."""

from typing import Optional


def parse_rates_command_tokens(tokens: list[str]) -> tuple[bool, str, Optional[float]]:
    """
    /rates [refresh] [filter NAME] [RECEIVING_THB] — порядок refresh / filter любой.

    Краткая форма: /rates PRESET — один пресет без слова «filter»
    (например ``/rates ta`` эквивалентно ``/rates filter ta``).

    Если среди аргументов есть положительное число, оно трактуется как
    ``--receiving-thb`` (пример: ``/rates 30000``).

    Неизвестный пресет обрабатывается тихи при сборке сводки.
    """
    if not tokens:
        return False, "", None
    body = list(tokens[1:])
    tlow = [x.lower() for x in body]
    refresh = any(x in ("refresh", "r", "--refresh") for x in tlow)
    rest = [body[i] for i in range(len(body)) if tlow[i] not in ("refresh", "r", "--refresh")]
    output_filter = ""
    receiving_thb: Optional[float] = None
    free_tokens: list[str] = []

    i = 0
    while i < len(rest):
        tok = rest[i]
        low = tok.lower()
        if low == "filter":
            if i + 1 < len(rest):
                cand = rest[i + 1].strip()
                if cand.lower() not in ("refresh", "r", "--refresh", "filter"):
                    output_filter = cand
            i += 2
            continue
        free_tokens.append(tok)
        i += 1

    for tok in free_tokens:
        s = tok.strip()
        if not s:
            continue
        try:
            val = float(s)
        except ValueError:
            if not output_filter and s.lower() != "filter":
                output_filter = s
            continue
        if val > 0 and receiving_thb is None:
            receiving_thb = val

    return refresh, output_filter, receiving_thb
