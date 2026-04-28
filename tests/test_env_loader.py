# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from env_loader import load_repo_dotenv, patch_repo_dotenv


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

    def test_patch_repo_dotenv_replaces_and_appends(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / ".env"
            path.write_text(
                "SBER_QR_UFS_TOKEN=old\n"
                "# comment\n"
                "SBER_QR_HOSTNAME=old-host.invalid:9999\n"
                "OTHER=1\n",
                encoding="utf-8",
            )
            self.assertTrue(
                patch_repo_dotenv(
                    root,
                    {
                        "SBER_QR_UFS_TOKEN": 'new"tok',
                        "SBER_QR_UFS_SESSION": "session_fake_value",
                        "SBER_QR_HOSTNAME": "api-node.invalid:1234",
                    },
                )
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn('SBER_QR_UFS_TOKEN="new\\"tok"', text)
            self.assertIn('SBER_QR_UFS_SESSION="session_fake_value"', text)
            self.assertIn(
                "SBER_QR_HOSTNAME=\"api-node.invalid:1234\"",
                text,
            )
            self.assertIn("OTHER=1", text)
            self.assertIn("# comment", text)


if __name__ == "__main__":
    unittest.main()
