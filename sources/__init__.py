# -*- coding: utf-8 -*-
"""Плагины источников курса для :mod:`rates_sources`."""
from __future__ import annotations

from typing import Tuple

from rates_sources import RateSource

from . import askmoney
from . import avosend
from . import bybit_bitkub
from . import ex24
from . import forex
from . import korona
from . import kwikpay
from . import rshb_unionpay
from . import ttexchange

PLUGIN_ORDER = (
    "forex",
    "rshb_unionpay",
    "bybit_bitkub",
    "korona",
    "avosend",
    "ex24",
    "kwikpay",
    "askmoney",
    "ttexchange",
)

_MODS = {
    "forex": forex,
    "rshb_unionpay": rshb_unionpay,
    "bybit_bitkub": bybit_bitkub,
    "korona": korona,
    "avosend": avosend,
    "ex24": ex24,
    "kwikpay": kwikpay,
    "askmoney": askmoney,
    "ttexchange": ttexchange,
}


def load_default_sources() -> Tuple[RateSource, ...]:
    out: list[RateSource] = []
    for name in PLUGIN_ORDER:
        m = _MODS[name]
        out.append(
            RateSource(
                m.SOURCE_ID,
                m.EMOJI,
                m.IS_BASELINE,
                m.CATEGORY,
                m.summary,
            )
        )
    return tuple(out)
