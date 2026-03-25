# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "rates.py"), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


class TestRatesSummaryCli(unittest.TestCase):
    def test_top_level_help_lists_sources(self):
        r = _run("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        low = r.stdout.lower()
        self.assertIn("forex", low)
        self.assertIn("ttexchange", low)
        self.assertIn("sources", r.stdout)

    def test_source_help_only(self):
        r = _run("forex", "--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("XE", r.stdout)
        self.assertNotIn("korona", r.stdout.lower())

    def test_sources_subcommand_lists_ids(self):
        r = _run("sources")
        self.assertEqual(r.returncode, 0, r.stderr)
        for name in ("forex", "askmoney", "ex24", "ttexchange", "tbank"):
            self.assertIn(name, r.stdout)

    def test_save_writes_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out.txt"
            r = _run("save", str(out), "--refresh")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(out.is_file())
            text = out.read_text(encoding="utf-8")
            self.assertTrue("RUB" in text or "THB" in text)


if __name__ == "__main__":
    unittest.main()
