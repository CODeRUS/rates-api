# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from rates_parallel import default_max_workers, map_bounded


class TestRatesParallel(unittest.TestCase):
    def test_preserves_order_and_collects_exceptions(self) -> None:
        def work(x: int) -> int:
            if x == 2:
                raise ValueError("bad")
            return x * 10

        out = map_bounded([1, 2, 3], work, max_workers=3)
        self.assertEqual(len(out), 3)
        self.assertEqual(out[0], (1, 10, None))
        self.assertEqual(out[1][0], 2)
        self.assertIsNone(out[1][1])
        self.assertIsInstance(out[1][2], ValueError)
        self.assertEqual(out[2], (3, 30, None))

    def test_empty_sequence(self) -> None:
        self.assertEqual(map_bounded([], abs, max_workers=4), [])

    def test_default_max_workers_is_positive(self) -> None:
        self.assertGreaterEqual(default_max_workers(), 1)


if __name__ == "__main__":
    unittest.main()
