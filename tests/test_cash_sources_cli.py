# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import cash_report


class TestParseCashSources(unittest.TestCase):
    def test_all(self) -> None:
        self.assertEqual(cash_report.parse_cash_sources_str("all"), (True, True, True))

    def test_banki_only(self) -> None:
        self.assertEqual(cash_report.parse_cash_sources_str("banki"), (False, True, False))

    def test_combo(self) -> None:
        self.assertEqual(
            cash_report.parse_cash_sources_str("rbc,vbr"),
            (True, False, True),
        )

    def test_resolve_explicit_overrides_no_flags(self) -> None:
        self.assertEqual(
            cash_report.resolve_cash_sources_flags(
                sources="banki", no_banki=False, no_vbr=False
            ),
            (False, True, False),
        )

    def test_resolve_no_sources_uses_no_flags(self) -> None:
        self.assertEqual(
            cash_report.resolve_cash_sources_flags(
                sources=None, no_banki=True, no_vbr=True
            ),
            (True, False, False),
        )


class TestStripCashArgv(unittest.TestCase):
    def test_strip_and_inject(self) -> None:
        s, spec = cash_report._strip_standalone_cash_source_tokens(["1", "banki"])
        self.assertEqual(spec, "banki")
        n = cash_report._inject_cash_top_from_adjacent_ints(s)
        self.assertEqual(n, ["1"])

        s2, _ = cash_report._strip_standalone_cash_source_tokens(["1", "10"])
        n2 = cash_report._inject_cash_top_from_adjacent_ints(s2)
        self.assertEqual(n2, ["1", "--top", "10"])


if __name__ == "__main__":
    unittest.main()
