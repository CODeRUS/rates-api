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
            "_tab_ids_to_close",
            "_normalize_proxy_url",
            "_load_ex24_proxy_urls",
            "_select_page_tab",
            "debug_url_from_cmdline",
            "live_chromium_pid",
            "default_chromium_binary",
            "is_snap_chromium_exe",
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
                    "SNAP_CHROMIUM",
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
        self.assertIn("--user-data-dir=%s" % ns["DEFAULT_USER_DATA_DIR"], cmd)
        self.assertTrue(str(ns["DEFAULT_USER_DATA_DIR"]).endswith("/snap/chromium/common/chromium"))
        self.assertFalse(str(ns["DEFAULT_USER_DATA_DIR"]).endswith("chromium-multitransfer"))
        self.assertIn("--remote-debugging-port=9222", cmd)
        self.assertEqual(cmd[-1], "https://multitransfer.ru/transfer/thailand")

    def test_default_chromium_binary_is_snap(self) -> None:
        ns = _load_launch_helpers()
        self.assertEqual(ns["SNAP_CHROMIUM"], "/snap/bin/chromium")
        self.assertFalse(ns["is_snap_chromium_exe"]("/usr/lib/chromium-browser/chromium-browser"))
        self.assertFalse(ns["is_snap_chromium_exe"]("/usr/bin/chromium-browser"))
        self.assertTrue(
            ns["is_snap_chromium_exe"]("/snap/chromium/3548/usr/lib/chromium-browser/chrome")
        )
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "chromium"
            snap.write_text("", encoding="utf-8")
            self.assertEqual(ns["default_chromium_binary"](str(snap)), str(snap))
        with self.assertRaises(FileNotFoundError):
            ns["default_chromium_binary"]("/no/such/chromium-browser")

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

    def test_select_page_tab_does_not_reuse_unrelated_page(self) -> None:
        ns = _load_launch_helpers()
        pages = [
            {"type": "page", "url": "https://bereza-exchange.com/"},
            {
                "type": "page",
                "url": "https://multitransfer.ru/transfer/thailand?transfer=1",
            },
        ]
        found = ns["_select_page_tab"](
            pages,
            "multitransfer",
            prefer_url="https://multitransfer.ru/transfer/thailand",
            fallback=False,
        )
        self.assertEqual(found["url"], pages[1]["url"])
        self.assertIsNone(
            ns["_select_page_tab"](pages[:1], "multitransfer", prefer_url=ns["TARGET_URL"], fallback=False)
        )

    def test_debug_url_from_single_cmdline_string(self) -> None:
        ns = _load_launch_helpers()
        raw = (
            b"chromium-browser --window-size=1920,1080 --remote-debugging-port=9222\x00"
        )
        self.assertEqual(ns["debug_url_from_cmdline"](raw), "http://127.0.0.1:9222")

    def test_plain_start_matches_manual_snap_launch(self) -> None:
        ns = _load_launch_helpers()
        cmd = ns["_chromium_start_cmd"](
            chromium_binary="/snap/bin/chromium",
            debug_url="http://127.0.0.1:9222",
            start_url=ns["TARGET_URL"],
            user_data_dir=ns["DEFAULT_USER_DATA_DIR"],
            profile_directory="Default",
            plain=True,
        )
        self.assertEqual(
            cmd,
            [
                "/snap/bin/chromium",
                "--window-position=0,0",
                "--window-size=1920,1080",
                "--remote-debugging-port=9222",
                "https://multitransfer.ru/transfer/thailand",
            ],
        )

    def test_plain_start_puts_proxy_before_url(self) -> None:
        ns = _load_launch_helpers()
        cmd = ns["_chromium_start_cmd"](
            chromium_binary="/snap/bin/chromium",
            debug_url="http://127.0.0.1:9222",
            start_url=ns["TARGET_URL"],
            user_data_dir=ns["DEFAULT_USER_DATA_DIR"],
            profile_directory="Default",
            proxy_server="http://10.0.0.8:3128",
            plain=True,
        )
        self.assertEqual(cmd[-2], "--proxy-server=http://10.0.0.8:3128")
        self.assertEqual(cmd[-1], ns["TARGET_URL"])

    def test_load_ex24_proxy_file(self) -> None:
        ns = _load_launch_helpers()
        self.assertEqual(ns["_normalize_proxy_url"]("10.0.0.8:3128"), "http://10.0.0.8:3128")
        self.assertEqual(ns["_normalize_proxy_url"]("socks5://10.0.0.8:1080"), "socks5://10.0.0.8:1080")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proxies"
            path.write_text("# c\n\n10.0.0.1:3128\nhttp://10.0.0.2:8080\n", encoding="utf-8")
            self.assertEqual(
                ns["_load_ex24_proxy_urls"](path),
                ["http://10.0.0.1:3128", "http://10.0.0.2:8080"],
            )

    def test_amount_is_typed_into_debit_field(self) -> None:
        text = _SRC.read_text(encoding="utf-8")
        self.assertIn('data-testid="transfer_amount_debit-field"] input', text)
        self.assertIn('"via": "keyboard"', text)
        self.assertNotIn("Page.addScriptToEvaluateOnNewDocument", text)
        self.assertNotIn("queue(Number(digits), 'RUB', 'THB')", text)
        self.assertNotIn("FocusEvent('blur'", text)
        self.assertIn("functionality did not fire, reloading tab", text)
        self.assertIn("Page.reload", text)
        self.assertIn("_INPUT_DELAY_SEC = 20.0", text)
        self.assertIn("if tab is not None and not keep_open:", text)
        self.assertIn("_close_script_tabs", text)

    def test_tab_ids_to_close_keeps_other_sites(self) -> None:
        ns = _load_launch_helpers()
        pages = [
            {"type": "page", "id": "own", "url": "about:blank"},
            {"type": "page", "id": "th1", "url": "https://multitransfer.ru/transfer/thailand"},
            {"type": "page", "id": "th2", "url": "https://multitransfer.ru/transfer/thailand?x=1"},
            {"type": "page", "id": "home", "url": "https://multitransfer.ru/"},
            {"type": "page", "id": "other", "url": "https://bereza-exchange.com/"},
        ]
        self.assertEqual(
            ns["_tab_ids_to_close"](pages, ns["TARGET_URL"], "own"),
            ["own", "th1", "th2"],
        )
        self.assertEqual(
            ns["_tab_ids_to_close"](pages, ns["TARGET_URL"], ""),
            ["th1", "th2"],
        )


if __name__ == "__main__":
    unittest.main()
