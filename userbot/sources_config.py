# -*- coding: utf-8 -*-
"""
Конфиг каналов/чатов для userbot.

Где настраивать:
  - chat: username канала (@name) или числовой chat id
  - name: как источник будет называться в сводке
  - source_id: стабильный id источника в кеше
  - category:
      transfer / cash_rub / cash_usd / cash_eur / cash_cny
  - pattern: regex с именованной группой (?P<rate>...)
"""
from __future__ import annotations

from userbot.models import CurrencyRule, SourceConfig


USERBOT_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        source_id="unired_bkb",
        name="Unired",
        chat="-1001380405475",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="transfer",
                pattern=r"Россиядан\s*-\s*VISAга[\s\S]*?1\s*\$\s*=\s*(?P<rate>\d+(?:[.,]\d+)?)\s*RUB",
            ),
        ),
    ),
    SourceConfig(
        source_id="fintrust_exchange",
        name="Fintrust Exchange",
        chat="-1001571066333",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_usd",
                pattern=r"💵\s*Продажа[\s\S]*?⚪️\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="EUR",
                category="cash_eur",
                pattern=r"💶\s*Покупка[\s\S]*?-\s*(?P<rate>\d+(?:[.,]\d+)?)\s*(?:\(|$)",
            ),
        ),
    ),
)

