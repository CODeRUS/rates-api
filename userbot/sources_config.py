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
        chat="@uniredmobile",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="transfer",
                pattern=r"Россиядан\s*-\s*VISAга[\s\S]*?1\s*\$\s*=\s*(?P<rate>\d+(?:[.,]\d+)?)\s*RUB",
            ),
        ),
        city="",
    ),
    SourceConfig(
        source_id="fintrust_exchange",
        name="Fintrust Exchange",
        chat="@fintrust",
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
        city="Москва",
    ),
    SourceConfig(
        source_id="sovcomrates_msk",
        name="Совкомбанк",
        chat="@sovcomrates_msk",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_usd",
                # Берем строку валюты целиком, чтобы матч не "утекал" на соседние строки.
                # Символы стрелок/маркеры изменения опциональны.
                pattern=r"^\s*USD[\s\u00A0]+(?:\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?[\s\u00A0]+(?P<rate>\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?\s*$",
            ),
            CurrencyRule(
                currency="EUR",
                category="cash_eur",
                pattern=r"^\s*EUR[\s\u00A0]+(?:\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?[\s\u00A0]+(?P<rate>\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?\s*$",
            ),
            CurrencyRule(
                currency="CNY",
                category="cash_cny",
                pattern=r"^\s*CNY[\s\u00A0]+(?:\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?[\s\u00A0]+(?P<rate>\d+(?:[.,]\d+)?)(?:[\s\u00A0]*[^\d\s])?\s*$",
            ),
        ),
        city="Москва",
    ),
    SourceConfig(
        source_id="vernadsky_msk",
        name="Вернадский",
        chat="@Vernadka39",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_usd",
                # Берем RUB-USD из блока "1996–2006" → колонка "Продажа"
                pattern=r"Серия\s*купюр\s*1996[–-]2006[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="EUR",
                category="cash_eur",
                # Берем RUB-EUR из блока "2002" → колонка "Продажа"
                pattern=r"Серия\s*купюр\s*2002[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="CNY",
                category="cash_cny",
                # Берем RUB-CNY из секции "Китайский юань (¥)" → колонка "Продажа"
                pattern=r"Китайский\s*юань[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
        ),
        city="Москва",
    ),
    SourceConfig(
        source_id="obuv_city_msk",
        name="Обувь-Сити",
        chat="@Vernadka14a",
        emoji="•",
        currencies=(
            CurrencyRule(
                currency="USD",
                category="cash_usd",
                # Берем RUB-USD из блока "Серия купюр 1996–2006" → колонка "Продажа"
                pattern=r"Серия\s*купюр\s*1996[–-]2006[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="EUR",
                category="cash_eur",
                # Берем RUB-EUR из блока "Серия купюр 2002" → колонка "Продажа"
                pattern=r"Серия\s*купюр\s*2002[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="CNY",
                category="cash_cny",
                # Берем RUB-CNY из секции "Китайский юань" → колонка "Продажа"
                pattern=r"Китайский\s*юань[\s\u00A0\S]*?Продажа\s*[:：]?\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
        ),
        city="Москва",
    ),
    SourceConfig(
        source_id="it_obmen_pattaya",
        name="IT Обмен",
        chat="@it_obmen_pattaya",
        emoji="🤑",
        currencies=(
            CurrencyRule(
                currency="RUBTHB",
                category="transfer",
                # Пример:
                # Онлайн Рубль -> Наличный Бат
                # от 5к Бат  –  2.68
                # от 20к Бат – 2.67   <- берем этот курс
                # от 50к Бат – 2.66
                pattern=r"Онлайн\s*Рубль\s*[-–>]+\s*Наличный\s*Бат[\s\S]*?от\s*20к\s*Бат\s*[–-]\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="USDTTHB",
                category="usdt_thb",
                # Пример:
                # до 1000 USDT – 31.3
                # свыше 1000 USDT – 31.4
                # Берем "до 1000 USDT".
                pattern=r"до\s*1000\s*USDT\s*[–-]\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
        ),
        city="",
        summary_note="от 20000 THB нал",
    ),
    SourceConfig(
        source_id="fly_currency",
        name="Fly Currency",
        chat="@ThaiExchangee",
        emoji="🤑",
        currencies=(
            CurrencyRule(
                currency="RUBTHB",
                category="transfer",
                # RUB -> THB: 2.62 – 2.68  => берем верхнюю границу 2.68
                pattern=r"RUB\s*(?:->|→)\s*THB\s*:\s*\d+(?:[.,]\d+)?\s*[–-]\s*(?P<rate>\d+(?:[.,]\d+)?)",
            ),
            CurrencyRule(
                currency="USDTTHB",
                category="usdt_thb",
                # USDT -> THB: 31.34–31.99 => берем нижнюю границу 31.34
                pattern=r"USDT\s*(?:->|→)\s*THB\s*:\s*(?P<rate>\d+(?:[.,]\d+)?)\s*[–-]\s*\d+(?:[.,]\d+)?",
            ),
        ),
        city="",
        summary_note="минимальная сумма",
    ),
)

