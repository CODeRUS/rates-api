# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from browser.bereza_cf_cdp import (
    DEFAULT_USER_DATA_DIR,
    SNAP_CHROMIUM,
    checkbox_click_point,
    clearance_usable,
    click_turnstile_checkbox,
    debug_url_from_cmdline,
    default_chromium_binary,
    find_bereza_tab,
    is_cloudflare_stub,
    is_turnstile_iframe,
    _pass_cloudflare_stub,
)


class TestBerezaCfExport(unittest.TestCase):
    def test_cached_title_without_token_is_rejected(self) -> None:
        self.assertFalse(clearance_usable("Bereza Exchange", 403, "cookie"))
        self.assertFalse(clearance_usable("Just a moment...", 200, "cookie"))
        self.assertFalse(clearance_usable("Bereza Exchange", 200, "  "))
        self.assertFalse(clearance_usable("", 200, "cookie"))

    def test_live_token_is_accepted(self) -> None:
        self.assertTrue(clearance_usable("Bereza Exchange", 200, "cookie"))

    def test_find_bereza_tab_skips_other_pages(self) -> None:
        tabs = [
            {"type": "page", "url": "https://multitransfer.ru/transfer/thailand"},
            {"type": "page", "url": "https://bereza-exchange.com/"},
        ]
        found = find_bereza_tab(tabs)
        self.assertEqual(found["url"], "https://bereza-exchange.com/")
        self.assertIsNone(find_bereza_tab(tabs[:1]))

    def test_debug_url_from_cmdline(self) -> None:
        self.assertEqual(
            debug_url_from_cmdline([b"chromium", b"--remote-debugging-port=9222"]),
            "http://127.0.0.1:9222",
        )
        self.assertEqual(
            debug_url_from_cmdline(
                b"chromium-browser --window-size=1920,1080 --remote-debugging-port=9222\x00"
            ),
            "http://127.0.0.1:9222",
        )
        self.assertIsNone(debug_url_from_cmdline([b"chromium"]))

    def test_default_browser_is_snap(self) -> None:
        self.assertEqual(SNAP_CHROMIUM, "/snap/bin/chromium")
        self.assertEqual(default_chromium_binary(), "/snap/bin/chromium")
        self.assertTrue(str(DEFAULT_USER_DATA_DIR).endswith("/snap/chromium/common/chromium"))
        with self.assertRaises(FileNotFoundError):
            default_chromium_binary("/usr/bin/chromium-browser-missing")

    def test_cloudflare_stub_titles(self) -> None:
        self.assertTrue(is_cloudflare_stub("Just a moment..."))
        self.assertTrue(is_cloudflare_stub("Performing security verification"))
        self.assertFalse(is_cloudflare_stub("Bereza Exchange"))
        self.assertFalse(clearance_usable("Performing security verification", 200, "cookie"))

    def test_checkbox_point_matches_turnstile_box(self) -> None:
        # Квад виджета 300x65, галочка слева: (511.5+28, 304+32.5).
        point = checkbox_click_point([511.5, 304, 811.5, 304, 811.5, 369, 511.5, 369])
        self.assertEqual(point, (539.5, 336.5))
        self.assertIsNone(checkbox_click_point([0, 0, 10, 0, 10, 10, 0, 10]))

    def test_turnstile_iframe_src(self) -> None:
        src = ["src", "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/av0"]
        self.assertTrue(is_turnstile_iframe("IFRAME", src))
        self.assertFalse(is_turnstile_iframe("IFRAME", ["src", "https://example.com/embed"]))

    def test_click_uses_checkbox_point(self) -> None:
        cdp = _TurnstileCDP(after_click_title="Just a moment...")
        self.assertTrue(click_turnstile_checkbox(cdp, timeout_sec=1.0))
        pressed = [params for method, params in cdp.calls if method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed"]
        self.assertEqual(pressed, [{"type": "mousePressed", "x": 539.5, "y": 336.5, "button": "left", "clickCount": 1}])

    def test_open_site_is_not_clicked(self) -> None:
        cdp = _TurnstileCDP(after_click_title="Bereza Exchange")
        title, status = _pass_cloudflare_stub(cdp, "Bereza Exchange", 200, timeout_sec=5.0)
        self.assertEqual((title, status), ("Bereza Exchange", 200))
        self.assertFalse(any(method == "Input.dispatchMouseEvent" for method, _params in cdp.calls))

    def test_stub_is_clicked_once(self) -> None:
        cdp = _TurnstileCDP(after_click_title="Bereza Exchange", token_status="200")
        title, status = _pass_cloudflare_stub(cdp, "Just a moment...", 0, timeout_sec=5.0)
        self.assertEqual(title, "Bereza Exchange")
        self.assertEqual(status, 200)
        clicks = [params for method, params in cdp.calls if method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed"]
        self.assertEqual(len(clicks), 1)


class _TurnstileCDP:
    def __init__(self, after_click_title: str, token_status: str = "200") -> None:
        self.calls = []
        self.after_click_title = after_click_title
        self.token_status = token_status
        self.clicked = False

    def call(self, method, params=None, timeout_sec=30.0):
        params = params or {}
        self.calls.append((method, params))
        if method == "DOM.getDocument":
            return {"root": {"nodeId": 1}}
        if method == "DOM.performSearch":
            return {"searchId": "s", "resultCount": 1}
        if method == "DOM.getSearchResults":
            return {"nodeIds": [7]}
        if method == "DOM.describeNode":
            return {
                "node": {
                    "nodeName": "IFRAME",
                    "attributes": [
                        "src",
                        "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/turnstile/f/av0",
                    ],
                }
            }
        if method == "DOM.getBoxModel":
            return {"model": {"content": [511.5, 304, 811.5, 304, 811.5, 369, 511.5, 369]}}
        if method == "DOM.discardSearchResults":
            return {}
        if method == "Input.dispatchMouseEvent":
            if params.get("type") == "mouseReleased":
                self.clicked = True
            return {}
        if method == "Runtime.evaluate":
            expr = params.get("expression") or ""
            if "scrollX" in expr:
                return {"result": {"value": '{"x":0,"y":0}'}}
            if "await fetch" in expr:
                return {"result": {"value": self.token_status}}
            title = self.after_click_title if self.clicked else "Just a moment..."
            return {"result": {"value": '{"title":"%s","ready":"complete"}' % title}}
        return {}
