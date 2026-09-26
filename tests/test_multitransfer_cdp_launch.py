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
        if isinstance(node, ast.ImportFrom) and node.module == "pathlib":
            keep.append(node)
        elif isinstance(node, ast.Import) and any(
            a.name in {"urllib.parse", "os", "json"} for a in node.names
        ):
            keep.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in (
            "_debug_port_from_url",
            "_chromium_start_cmd",
            "_prefer_named_profile",
        ):
            keep.append(node)
        elif isinstance(node, ast.Assign):
            names = [
                t.id for t in node.targets if isinstance(t, ast.Name)
            ]
            if any(
                n in {"DEFAULT_USER_DATA_DIR", "DEFAULT_PROFILE_DIRECTORY"}
                for n in names
            ):
                keep.append(node)
    mod = ast.Module(body=keep, type_ignores=[])
    ast.fix_missing_locations(mod)
    ns: dict = {}
    exec(compile(mod, str(_SRC), "exec"), ns, ns)
    return ns


class TestChromiumStartCmd(unittest.TestCase):
    def test_uses_named_profile_not_guest(self) -> None:
        ns = _load_launch_helpers()
        cmd = ns["_chromium_start_cmd"](
            chromium_binary="chromium-browser",
            debug_url="http://127.0.0.1:9222",
            start_url="https://multitransfer.ru",
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
        self.assertEqual(cmd[-1], "https://multitransfer.ru")

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


if __name__ == "__main__":
    unittest.main()
