# -*- coding: utf-8 -*-
"""Статический список источников для команды /sources."""
from __future__ import annotations

from typing import Dict, List, Tuple

_TITLES: Tuple[Tuple[str, str], ...] = (
    ("transfer", "Переводы"),
    ("exchanger", "Обменники"),
    ("cash_rub", "Наличные RUB"),
    ("cash_usd", "Наличные USD"),
    ("cash_eur", "Наличные EUR"),
    ("cash_cny", "Наличные CNY"),
)

# (category, name, urls). Порядок внутри категории задаёт format_sources_message.
_ENTRIES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    ("transfer", "Avosend", ("https://avosend.com/",)),
    ("transfer", "Bangkok Bank", ("https://www.bangkokbank.com/",)),
    ("transfer", "Binance TH", ("https://www.binance.th/en/trade/USDT_THB",)),
    ("transfer", "Bitkub", ("https://www.bitkub.com/en/market/USDT_THB",)),
    ("transfer", "Bybit", ("https://www.bybit.com/fiat/trade/otc/",)),
    ("transfer", "Forex", ("https://www.xe.com/",)),
    ("transfer", "HTX", ("https://www.htx.com/",)),
    ("transfer", "KwikPay", ("https://kwikpay.ru/",)),
    ("transfer", "Moreta", ("https://moretapay.com/",)),
    ("transfer", "NovaWallet", ("https://novawallet.org/",)),
    ("transfer", "Payscan", ("https://payscan.ru/",)),
    ("transfer", "Unired", ("https://t.me/uniredmobile",)),
    ("transfer", "Мультитрансфер", ("https://multitransfer.ru/",)),
    (
        "transfer",
        "РСХБ UnionPay",
        (
            "https://www.rshb.ru/",
            "https://www.unionpayintl.com/",
            "https://www.moex.com/",
        ),
    ),
    ("transfer", "Сбербанк QR", ("https://www.sberbank.ru/",)),
    ("exchanger", "askmoney", ("https://askmoney.pro/",)),
    (
        "exchanger",
        "Bereza",
        (
            "https://bereza-exchange.com/",
            "https://t.me/bereza_exchange/1631",
        ),
    ),
    ("exchanger", "Ex24", ("https://ex24.pro/",)),
    ("exchanger", "Exasia", ("https://t.me/exthailand",)),
    ("exchanger", "Fly Currency", ("https://t.me/ThaiExchangee",)),
    ("exchanger", "IT Обмен", ("https://t.me/it_obmen_pattaya",)),
    ("exchanger", "Senate", ("https://senateexchange.org/",)),
    ("exchanger", "XAACTA", ("https://t.me/XAACTAExchange",)),
    ("cash_rub", "Bereza", ("https://bereza-exchange.com/",)),
    ("cash_rub", "Ex24", ("https://ex24.pro/",)),
    ("cash_rub", "TT Currency Exchange", ("https://ttexchange.com/",)),
    ("cash_rub", "Т-Банк", ("https://www.tbank.ru/",)),
    ("cash_usd", "Ex24", ("https://ex24.pro/",)),
    ("cash_usd", "Fintrust Exchange", ("https://t.me/fintrust",)),
    ("cash_usd", "TT Currency Exchange", ("https://ttexchange.com/",)),
    ("cash_usd", "Вернадский", ("https://profikassa.ru/",)),
    ("cash_usd", "Обувь-Сити", ("https://vernadka-kassa.ru/",)),
    ("cash_usd", "Совкомбанк", ("https://t.me/sovcomrates_msk",)),
    ("cash_eur", "Ex24", ("https://ex24.pro/",)),
    ("cash_eur", "Fintrust Exchange", ("https://t.me/fintrust",)),
    ("cash_eur", "TT Currency Exchange", ("https://ttexchange.com/",)),
    ("cash_eur", "Вернадский", ("https://profikassa.ru/",)),
    ("cash_eur", "Обувь-Сити", ("https://vernadka-kassa.ru/",)),
    ("cash_eur", "Совкомбанк", ("https://t.me/sovcomrates_msk",)),
    ("cash_cny", "Ex24", ("https://ex24.pro/",)),
    ("cash_cny", "TT Currency Exchange", ("https://ttexchange.com/",)),
    ("cash_cny", "Вернадский", ("https://profikassa.ru/",)),
    ("cash_cny", "Обувь-Сити", ("https://vernadka-kassa.ru/",)),
    ("cash_cny", "Совкомбанк", ("https://t.me/sovcomrates_msk",)),
)


def format_sources_message() -> str:
    """Категория, затем каждый источник с новой строки и ссылки под ним."""
    grouped: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {key: [] for key, _ in _TITLES}
    for category, name, urls in _ENTRIES:
        grouped[category].append((name, urls))
    blocks: List[str] = []
    for key, title in _TITLES:
        rows = sorted(grouped[key], key=lambda row: row[0].casefold())
        if not rows:
            continue
        lines = [title]
        for name, urls in rows:
            lines.append(f"• {name}")
            lines.extend(urls)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
