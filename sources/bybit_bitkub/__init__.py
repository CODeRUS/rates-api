# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import List, Optional

from rates_sources import FetchContext, SourceCategory, SourceQuote, fmt_money_ru

SOURCE_ID = "bybit_bitkub"
EMOJI = "💸"
IS_BASELINE = False
CATEGORY = SourceCategory.TRANSFER

WARN_NO_CASH = (
    "Bybit: нет объявлений Cash Deposit (18) с completion≥99 "
    "(100 USDT, minAmount≥100·price)"
)
WARN_NO_TRANSFER = (
    "Bybit: нет объявлений только перевод (14, без 18) с completion≥99 "
    "(100 USDT, minAmount≥100·price)"
)
WARN_NO_BID = "Bitkub: нет highestBid для USDT"

_LABEL_CASH = "Bybit P2P (cash) → Bitkub"
_LABEL_TRANSFER = "Bybit P2P (перевод) → Bitkub"


def help_text() -> str:
    return (
        "Bybit P2P USDT/RUB + Bitkub THB/USDT.\n"
        "  quote …  — рубли или баты по курсу сводки (лот 100 USDT)\n"
        "             --receiving-thb N | --sending-rub N | --leg cash|transfer\n"
        "  bitkub …  — тикер Bitkub (подкоманда, см. bybit_bitkub bitkub --help)\n"
        "  иначе     — аргументы передаются в Bybit P2P CLI (bybit_p2p_usdt_rub)"
    )


def command(argv: list[str]) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        print(help_text())
        print(
            "\nПример: rates.py bybit_bitkub --json\n"
            "         rates.py bybit_bitkub quote --receiving-thb 800\n"
            "         rates.py bybit_bitkub bitkub --json",
            file=sys.stderr,
        )
        return 0
    if argv[0] == "quote":
        return quote_command(argv[1:])
    if argv[0] == "bitkub":
        from .bitkub_usdt_thb import cli_main

        return cli_main(argv[1:])
    from .bybit_p2p_usdt_rub import cli_main

    return cli_main(argv)


def _live_prices() -> tuple[Optional[float], Optional[float], float]:
    """RUB/USDT cash, RUB/USDT перевод, THB/USDT. Тот же отбор, что у summary()."""
    from . import bitkub_usdt_thb as bk
    from . import bybit_p2p_usdt_rub as bp

    items = bp.fetch_all_online_items(size=20, verification_filter=0)
    items = bp.filter_by_target_usdt(items, target_usdt=bp.DEFAULT_TARGET_USDT)
    a = bp.filter_cash_deposit_to_bank(items, 99.0)
    b = bp.filter_bank_transfer_no_cash(items, 99.0)
    ia = bp.min_by_price(a)
    ib = bp.min_by_price(b)
    tk = bk.fetch_ticker()
    thb_usdt = float(tk.get("highestBid") or 0)
    cash = float(ia["price"]) if ia else None
    transfer = float(ib["price"]) if ib else None
    return cash, transfer, thb_usdt


def _summary_quotes(
    ctx: FetchContext,
    cash_p: Optional[float],
    tr_p: Optional[float],
    thb_usdt: Optional[float],
) -> Optional[List[SourceQuote]]:
    if thb_usdt is None or thb_usdt <= 0:
        ctx.warnings.append(WARN_NO_BID)
        return None
    out: List[SourceQuote] = []
    if cash_p is not None and cash_p > 0:
        out.append(SourceQuote(cash_p / thb_usdt, _LABEL_CASH, merge_key="bybit_cash"))
    else:
        ctx.warnings.append(WARN_NO_CASH)
    if tr_p is not None and tr_p > 0:
        out.append(
            SourceQuote(tr_p / thb_usdt, _LABEL_TRANSFER, merge_key="bybit_transfer")
        )
    else:
        ctx.warnings.append(WARN_NO_TRANSFER)
    return out or None


def summary(ctx: FetchContext) -> Optional[List[SourceQuote]]:
    from rates_primitives import read_bitkub_bid, read_bybit_p2p

    doc = ctx.unified_doc
    if doc is not None:
        thb_usdt, w_bk = read_bitkub_bid(doc)
        cash_p, tr_p, w_bp = read_bybit_p2p(doc)
        ctx.warnings.extend(w_bk)
        ctx.warnings.extend(w_bp)
        return _summary_quotes(ctx, cash_p, tr_p, thb_usdt)

    cash_p, tr_p, thb_usdt = _live_prices()
    return _summary_quotes(ctx, cash_p, tr_p, thb_usdt if thb_usdt > 0 else None)


@dataclass(frozen=True)
class LegQuote:
    key: str
    label: str
    rub_per_thb: float


def leg_rates_from_prices(
    cash_price: Optional[float],
    transfer_price: Optional[float],
    thb_per_usdt: Optional[float],
) -> tuple[list[LegQuote], list[str]]:
    """Курс RUB/THB до 3 знаков — как в строке сводки, чтобы сумма совпала с умножением."""
    if thb_per_usdt is None or thb_per_usdt <= 0:
        return [], [WARN_NO_BID]
    out: list[LegQuote] = []
    warns: list[str] = []
    if cash_price is not None and cash_price > 0:
        rate = round(float(cash_price) / float(thb_per_usdt), 3)
        if rate > 0:
            out.append(LegQuote("cash", "cash", rate))
        else:
            warns.append(WARN_NO_CASH)
    else:
        warns.append(WARN_NO_CASH)
    if transfer_price is not None and transfer_price > 0:
        rate = round(float(transfer_price) / float(thb_per_usdt), 3)
        if rate > 0:
            out.append(LegQuote("transfer", "перевод", rate))
        else:
            warns.append(WARN_NO_TRANSFER)
    else:
        warns.append(WARN_NO_TRANSFER)
    return out, warns


def _lot_line() -> str:
    from .bybit_p2p_usdt_rub import DEFAULT_TARGET_USDT

    return f"Курс с лота {DEFAULT_TARGET_USDT:g} USDT."


def _keep_warning(warning: str, leg: Optional[str]) -> bool:
    if leg is None or "highestBid" in warning:
        return True
    if leg == "cash":
        return "Cash Deposit" in warning
    return "только перевод" in warning


def format_bybit_bitkub_quote(
    *,
    cash_price: Optional[float],
    transfer_price: Optional[float],
    thb_per_usdt: Optional[float],
    extra_warnings: Optional[list[str]] = None,
    receiving_thb: Optional[int] = None,
    sending_rub: Optional[int] = None,
    leg: Optional[str] = None,
) -> str:
    legs, missing = leg_rates_from_prices(cash_price, transfer_price, thb_per_usdt)
    if leg == "cash":
        legs = [x for x in legs if x.key == "cash"]
    elif leg == "transfer":
        legs = [x for x in legs if x.key == "transfer"]
    missing = [w for w in missing if _keep_warning(w, leg)]
    extras = [w for w in (extra_warnings or []) if w and _keep_warning(w, leg)]

    if receiving_thb is not None:
        head = f"Bybit P2P → Bitkub, получить {fmt_money_ru(receiving_thb)} THB"
    elif sending_rub is not None:
        head = f"Bybit P2P → Bitkub, отправить {fmt_money_ru(sending_rub)} RUB"
    else:
        head = "Bybit P2P → Bitkub"
    lines = [head, _lot_line()]
    for q in legs:
        rate_s = f"{q.rub_per_thb:.3f}"
        if receiving_thb is not None:
            rub = fmt_money_ru(receiving_thb * q.rub_per_thb)
            lines.append(f"💸 {rub} RUB | {rate_s} RUB/THB | {q.label}")
        elif sending_rub is not None:
            thb = fmt_money_ru(sending_rub / q.rub_per_thb)
            lines.append(f"💸 {thb} THB | {rate_s} RUB/THB | {q.label}")
        else:
            lines.append(f"💸 {rate_s} RUB/THB | {q.label}")
    seen = set(lines)
    for w in extras + missing:
        if w not in seen:
            lines.append(w)
            seen.add(w)
    return "\n".join(lines) + "\n"


def resolve_bybit_bitkub_prices(
    *, allow_live: bool
) -> tuple[Optional[float], Optional[float], Optional[float], list[str]]:
    """Кеш примитивов, как у сводки. Live — только если обоих примитивов нет."""
    import rates_unified_cache as ucc
    from rates_primitives import (
        PRIM_BITKUB_USDT_THB,
        PRIM_BYBIT_P2P_RUB,
        read_bitkub_bid,
        read_bybit_p2p,
    )

    doc = ucc.load_unified()
    hit_bid = ucc.prim_get_valid(doc, PRIM_BITKUB_USDT_THB) if doc else None
    hit_p2p = ucc.prim_get_valid(doc, PRIM_BYBIT_P2P_RUB) if doc else None
    if hit_bid is not None or hit_p2p is not None:
        thb, w_bk = read_bitkub_bid(doc)
        cash, transfer, w_bp = read_bybit_p2p(doc)
        return cash, transfer, thb, list(w_bk) + list(w_bp)
    if not allow_live:
        return None, None, None, []
    cash, transfer, thb = _live_prices()
    return cash, transfer, thb if thb > 0 else None, []


def render_bybit_bitkub_quote(
    *,
    receiving_thb: Optional[int] = None,
    sending_rub: Optional[int] = None,
    leg: Optional[str] = None,
    allow_live: bool = True,
) -> str:
    cash, transfer, thb, warns = resolve_bybit_bitkub_prices(allow_live=allow_live)
    return format_bybit_bitkub_quote(
        cash_price=cash,
        transfer_price=transfer,
        thb_per_usdt=thb,
        extra_warnings=warns,
        receiving_thb=receiving_thb,
        sending_rub=sending_rub,
        leg=leg,
    )


def quote_command(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        prog="rates.py bybit_bitkub quote",
        description="Сколько RUB на N THB или сколько THB на N RUB через Bybit P2P → Bitkub.",
    )
    p.add_argument("--receiving-thb", type=int, default=None)
    p.add_argument("--sending-rub", type=int, default=None)
    p.add_argument("--leg", choices=("cash", "transfer"), default=None)
    p.add_argument("--readonly", action="store_true")
    try:
        ns = p.parse_args(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 1
    if ns.receiving_thb is not None and ns.sending_rub is not None:
        print(
            "укажите не больше одного из: --receiving-thb или --sending-rub",
            file=sys.stderr,
        )
        return 2
    for flag, val in (
        ("--receiving-thb", ns.receiving_thb),
        ("--sending-rub", ns.sending_rub),
    ):
        if val is not None and val < 1:
            print(f"{flag} должно быть больше 0", file=sys.stderr)
            return 2
    try:
        text = render_bybit_bitkub_quote(
            receiving_thb=ns.receiving_thb,
            sending_rub=ns.sending_rub,
            leg=ns.leg,
            allow_live=not ns.readonly,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0
