# -*- coding: utf-8 -*-
"""Плагины источников курса для :mod:`rates_sources`."""
from __future__ import annotations

from typing import Dict, Tuple

PLUGIN_ORDER = (
    "forex",
    "rshb_unionpay",
    "bybit_bitkub",
    "bybit_novawallet",
    "korona",
    "avosend",
    "avosend_bkb",
    "ex24",
    "kwikpay",
    "askmoney",
    "payscan",
    "ttexchange",
    "rbc_ttexchange",
    "tbank",
    "unired_bkb",
    "userbot_cash",
)

_MODS_CACHE: Dict[str, object] = {}


def _mods() -> Dict[str, object]:
    if not _MODS_CACHE:
        from . import askmoney
        from . import avosend
        from . import avosend_bkb
        from . import bybit_binanceth
        from . import bybit_bitkub
        from . import bybit_novawallet
        from . import ex24
        from . import forex
        from . import htx_binanceth
        from . import htx_bitkub
        from . import korona
        from . import kwikpay
        from . import payscan
        from . import rbc_ttexchange
        from . import rshb_unionpay
        from . import tbank
        from . import ttexchange
        from . import unired_bkb
        from . import userbot_cash

        _MODS_CACHE.update(
            {
                "forex": forex,
                "rshb_unionpay": rshb_unionpay,
                "bybit_bitkub": bybit_bitkub,
                "bybit_novawallet": bybit_novawallet,
                "htx_bitkub": htx_bitkub,
                "bybit_binanceth": bybit_binanceth,
                "htx_binanceth": htx_binanceth,
                "korona": korona,
                "avosend": avosend,
                "avosend_bkb": avosend_bkb,
                "ex24": ex24,
                "kwikpay": kwikpay,
                "askmoney": askmoney,
                "payscan": payscan,
                "ttexchange": ttexchange,
                "rbc_ttexchange": rbc_ttexchange,
                "tbank": tbank,
                "unired_bkb": unired_bkb,
                "userbot_cash": userbot_cash,
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
    """Порядок для сводки — :data:`PLUGIN_ORDER`; остальные плагины в конце (для CLI ``rates.py <id>``)."""
    mods = _mods()
    ordered = [mods[k].SOURCE_ID for k in PLUGIN_ORDER]
    extra_keys = [k for k in sorted(mods.keys()) if k not in PLUGIN_ORDER]
    extra = [mods[k].SOURCE_ID for k in extra_keys]
    return tuple(ordered + extra)
