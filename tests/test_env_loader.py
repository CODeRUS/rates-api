# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from env_loader import load_repo_dotenv


class TestEnvLoader(unittest.TestCase):
    def test_setdefault_does_not_override(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env").write_text("FOO_FROM_ENV=fromfile\n", encoding="utf-8")
            os.environ["FOO_FROM_ENV"] = "fromshell"
            try:
                load_repo_dotenv(root)
                self.assertEqual(os.environ.get("FOO_FROM_ENV"), "fromshell")
            finally:
                os.environ.pop("FOO_FROM_ENV", None)

    def test_loads_quoted_and_export(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".env").write_text(
                'export BAR=1\n'
                'BAZ="quoted"\n',
                encoding="utf-8",
            )
            os.environ.pop("BAR", None)
            os.environ.pop("BAZ", None)
            try:
                self.assertTrue(load_repo_dotenv(root))
                self.assertEqual(os.environ.get("BAR"), "1")
                self.assertEqual(os.environ.get("BAZ"), "quoted")
            finally:
                os.environ.pop("BAR", None)
                os.environ.pop("BAZ", None)


if __name__ == "__main__":
    unittest.main()
