# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path


_SRC = Path(__file__).resolve().parents[1] / "browser" / "multitransfer_cdp_raw.py"


def _load_launch_helpers():
    """Загрузить хелперы запуска без импорта websocket-client."""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    keep = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in {"pathlib", "__future__"}:
            keep.append(node)
        elif isinstance(node, ast.Import) and any(
            a.name in {"urllib.parse", "os", "json"} for a in node.names
        ):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "_debug_port_from_url",
            "_chromium_start_cmd",
            "_prefer_named_profile",
            "_clear_stale_chromium_singleton",
            "_url_contains",
        ):
            keep.append(node)
        elif isinstance(node, ast.Assign):
            names = [
                t.id for t in node.targets if isinstance(t, ast.Name)
            ]
            if any(
                n in {
                    "DEFAULT_USER_DATA_DIR",
                    "DEFAULT_PROFILE_DIRECTORY",
                    "TARGET_URL",
                }
                for n in names
            ):
                keep.append(node)
    try:
        mod = ast.Module(body=keep, type_ignores=[])
    except TypeError:
        mod = ast.Module(body=keep)
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, str(_SRC), "exec"), ns, ns)
    return ns


class TestChromiumStartCmd(unittest.TestCase):
    def test_uses_named_profile_not_guest(self) -> None:
        ns = _load_launch_helpers()
        cmd = ns["_chromium_start_cmd"](
            chromium_binary="chromium-browser",
            debug_url="http://127.0.0.1:9222",
            start_url=ns["TARGET_URL"],
            user_data_dir=ns["DEFAULT_USER_DATA_DIR"],
            profile_directory="Default",
        )
        self.assertNotIn("--guest", cmd)
        self.assertIn("--hide-profile-picker-on-startup", cmd)
        self.assertIn("--disable-features=ProfilePickerOnStartup", cmd)
        self.assertIn("--profile-directory=Default", cmd)
        self.assertTrue(
            any(a.startswith("--user-data-dir=") and a.endswith("/.config/chromium") for a in cmd)
        )
        self.assertIn("--remote-debugging-port=9222", cmd)
        self.assertEqual(cmd[-1], "https://multitransfer.ru/transfer/thailand")

    def test_default_start_url_is_thailand_transfer(self) -> None:
        ns = _load_launch_helpers()
        self.assertEqual(
            ns["TARGET_URL"],
            "https://multitransfer.ru/transfer/thailand",
        )

    def test_prefer_named_profile_clears_guest_last_used(self) -> None:
        ns = _load_launch_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            user_data = Path(tmp)
            local_state = user_data / "Local State"
            local_state.write_text(
                json.dumps(
                    {
                        "profile": {
                            "last_used": "Guest Profile",
                            "last_active_profiles": ["Guest Profile"],
                        }
                    }
                ),
                encoding="utf-8",
            )
            ns["_prefer_named_profile"](user_data, "Default")
            data = json.loads(local_state.read_text(encoding="utf-8"))
            self.assertEqual(data["profile"]["last_used"], "Default")
            self.assertEqual(data["profile"]["last_active_profiles"], ["Default"])

    def test_clears_stale_singleton_lock(self) -> None:
        ns = _load_launch_helpers()
        with tempfile.TemporaryDirectory() as tmp:
            user_data = Path(tmp)
            lock = user_data / "SingletonLock"
            cookie = user_data / "SingletonCookie"
            lock.symlink_to("host-2147483647")
            cookie.write_text("x", encoding="utf-8")
            ns["_clear_stale_chromium_singleton"](user_data)
            self.assertFalse(lock.exists())
            self.assertFalse(cookie.exists())

    def test_url_contains_transfer_path(self) -> None:
        ns = _load_launch_helpers()
        self.assertTrue(
            ns["_url_contains"](
                "https://multitransfer.ru/transfer/thailand",
                "https://multitransfer.ru/transfer/thailand",
            )
        )
        self.assertFalse(
            ns["_url_contains"](
                "https://multitransfer.ru/",
                "https://multitransfer.ru/transfer/thailand",
            )
        )

    def test_amount_onchange_keeps_focus(self) -> None:
        text = _SRC.read_text(encoding="utf-8")
        self.assertIn("transfer_widget_debit-amount-field_input", text)
        self.assertIn("addToQueue", text)
        self.assertNotIn("FocusEvent('blur'", text)


if __name__ == "__main__":
    unittest.main()
