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


def plugin_by_id(source_id: str):
    """Модуль плагина по ``SOURCE_ID`` (например ``forex``) или ``None``."""
    m = _MODS.get(source_id)
    if m is not None and getattr(m, "SOURCE_ID", None) == source_id:
        return m
    for mod in _MODS.values():
        if getattr(mod, "SOURCE_ID", None) == source_id:
            return mod
    return None


def registered_source_ids() -> Tuple[str, ...]:
    return tuple(_MODS[k].SOURCE_ID for k in PLUGIN_ORDER)
