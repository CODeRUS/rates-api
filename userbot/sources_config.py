# -*- coding: utf-8 -*-
"""
Конфиг каналов/чатов для userbot.

Где настраивать:
  - chat: username канала (@name) или числовой chat id
  - name: как источник будет называться в сводке
  - source_id: стабильный id источника в кеше
  - category: в какой блок cash попадёт строка
      cash_rub / cash_usd / cash_eur / cash_cny
  - pattern: regex с именованной группой (?P<rate>...)
"""
from __future__ import annotations

from userbot.models import CurrencyRule, SourceConfig


USERBOT_SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        source_id="unired_chat",
        name="Unired",
        chat="-1001380405475",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_rub",
                pattern=r"RUB\s*[-/]\s*USD[^\d]*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
        ),
    ),
    SourceConfig(
        source_id="sample_channel_a",
        name="Sample Channel A",
        chat="@sample_channel_a",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_rub",
                pattern=r"USD\s*[:\-]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="EUR",
                category="cash_rub",
                pattern=r"EUR\s*[:\-]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
        ),
    ),
)

