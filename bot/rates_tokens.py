# -*- coding: utf-8 -*-
"""Разбор токенов команды /rates для бота."""


def parse_rates_command_tokens(tokens: list[str]) -> tuple[bool, str]:
    """
    /rates [refresh] [filter NAME] — порядок refresh / filter любой.

    Краткая форма: /rates PRESET — один пресет без слова «filter»
    (например ``/rates ta`` эквивалентно ``/rates filter ta``).

    Неизвестный пресет обрабатывается тихи при сборке сводки.
    """
    if not tokens:
        return False, ""
    body = list(tokens[1:])
    tlow = [x.lower() for x in body]
    refresh = any(x in ("refresh", "r", "--refresh") for x in tlow)
    rest = [body[i] for i in range(len(body)) if tlow[i] not in ("refresh", "r", "--refresh")]
    output_filter = ""
    rlow = [x.lower() for x in rest]
    try:
        fi = rlow.index("filter")
        if fi + 1 < len(rest):
            cand = rest[fi + 1].strip()
            if cand.lower() not in ("refresh", "r", "--refresh", "filter"):
                output_filter = cand
    except ValueError:
        if len(rest) == 1 and rest[0].lower() != "filter":
            output_filter = rest[0].strip()
    return refresh, output_filter
