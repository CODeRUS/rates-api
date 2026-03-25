# -*- coding: utf-8 -*-
"""Плагины источников курса для :mod:`rates_sources`."""
from __future__ import annotations

from typing import Dict, Tuple

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
    "tbank",
)

_MODS_CACHE: Dict[str, object] = {}


def _mods() -> Dict[str, object]:
    if not _MODS_CACHE:
        from . import askmoney
        from . import avosend
        from . import bybit_bitkub
        from . import ex24
        from . import forex
        from . import korona
        from . import kwikpay
        from . import rshb_unionpay
        from . import tbank
        from . import ttexchange

        _MODS_CACHE.update(
            {
                "forex": forex,
                "rshb_unionpay": rshb_unionpay,
                "bybit_bitkub": bybit_bitkub,
                "korona": korona,
                "avosend": avosend,
                "ex24": ex24,
                "kwikpay": kwikpay,
                "askmoney": askmoney,
                "ttexchange": ttexchange,
                "tbank": tbank,
            }
        )
    return _MODS_CACHE


def load_default_sources():
    from rates_sources import RateSource

    out = []
    mods = _mods()
    for name in PLUGIN_ORDER:
        m = mods[name]
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
    mods = _mods()
    m = mods.get(source_id)
    if m is not None and getattr(m, "SOURCE_ID", None) == source_id:
        return m
    for mod in mods.values():
        if getattr(mod, "SOURCE_ID", None) == source_id:
            return mod
    return None


def registered_source_ids() -> Tuple[str, ...]:
    mods = _mods()
    return tuple(mods[k].SOURCE_ID for k in PLUGIN_ORDER)
