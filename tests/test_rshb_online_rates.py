# -*- coding: utf-8 -*-
"""JSON /api/v1/rates: парсинг и cny_rur_sell."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from sources.rshb_unionpay import rshb_online_rates as online


_FIXTURE = r"""[[{"currencyPair":"CNY/RUB_TOD","buyRate":10.7700000000,"sellRate":11.3200000000,"lastUpdatedAt":"2026-04-24T16:40:18.125673+03:00"},{"currencyPair":"EUR/RUB_TOD","buyRate":82.8300000000,"sellRate":91.0200000000,"lastUpdatedAt":"2026-04-24T16:40:18.072581+03:00"},{"currencyPair":"USD/RUB_TOD","buyRate":71.0600000000,"sellRate":78.1600000000,"lastUpdatedAt":"2026-04-24T16:40:18.212263+03:00"}]]"""


def test_parse_rates_json_nested_array() -> None:
    tabs = online.parse_rates_json(_FIXTURE)
    assert set(tabs.keys()) == {date(2026, 4, 24)}
    rows = tabs[date(2026, 4, 24)]
    pairs = {q.pair.replace(" ", "").upper(): q.sell for q in rows}
    assert pairs["CNY/RUB"] == Decimal("11.32")
    assert pairs["USD/RUB"] == Decimal("78.16")


def test_cny_rur_sell_from_fixture() -> None:
    got = online.cny_rur_sell(html=_FIXTURE)
    assert got == Decimal("11.32")


def test_cny_rur_sell_matching_date() -> None:
    got = online.cny_rur_sell(on=date(2026, 4, 24), html=_FIXTURE)
    assert got == Decimal("11.32")


def test_cny_rur_sell_wrong_date_raises() -> None:
    with pytest.raises(KeyError, match="исторические даты endpoint не поддерживает"):
        online.cny_rur_sell(on=date(2020, 1, 1), html=_FIXTURE)


def test_get_table_for_date_on() -> None:
    rows = online.get_table_for_date(_FIXTURE, on=date(2026, 4, 24))
    assert len(rows) == 3


def test_parse_rates_json_invalid_raises() -> None:
    with pytest.raises(ValueError, match="JSON"):
        online.parse_rates_json("not json")


def test_parse_rates_json_empty_rows() -> None:
    assert online.parse_rates_json("[]") == {}
    assert online.parse_rates_json("[[]]") == {}
