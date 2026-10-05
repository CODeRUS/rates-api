#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Снять cf_clearance Bereza из уже пройденного Chromium-профиля и записать
``.rates_cache/bereza_cf.json`` для источника ``bereza``.

Запуск как у multitransfer (профиль ``~/.config/chromium``, DISPLAY :1)::

    python3.7 browser/bereza_cf_cdp.py --start-browser

Cron (со сдвигом от multitransfer, тот же профиль нельзя открывать одновременно)::

    30 */2 * * * cd /home/coderus/rates-api && /usr/bin/python3.7 browser/bereza_cf_cdp.py --start-browser
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.error
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
_CDP_PATH = Path(__file__).resolve().parent / "multitransfer_cdp_raw.py"
DEFAULT_OUT = _ROOT / ".rates_cache" / "bereza_cf.json"
TARGET_URL = "https://bereza-exchange.com/"
DEFAULT_DEBUG_URL = "http://127.0.0.1:9223"


def _cdp():
    spec = importlib.util.spec_from_file_location("multitransfer_cdp_raw", str(_CDP_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("не удалось загрузить browser/multitransfer_cdp_raw.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clearance_from_cookies(cookies: Any) -> Optional[str]:
    if not isinstance(cookies, list):
        return None
    for item in cookies:
        if not isinstance(item, dict):
            continue
        if item.get("name") != "cf_clearance":
            continue
        value = item.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _page_title(cdp, timeout_sec: float) -> str:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": "document.title || ''", "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    value = (result.get("result") or {}).get("value")
    return value if isinstance(value, str) else ""


def _user_agent(cdp, timeout_sec: float) -> str:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": "navigator.userAgent || ''", "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    value = (result.get("result") or {}).get("value")
    return value.strip() if isinstance(value, str) else ""


def _wait_past_challenge(cdp, timeout_sec: float) -> str:
    deadline = time.monotonic() + timeout_sec
    title = ""
    while time.monotonic() < deadline:
        title = _page_title(cdp, timeout_sec=10.0)
        if title and "just a moment" not in title.lower():
            return title
        time.sleep(1.0)
    return title


def _save(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    tmp.replace(path)


def run(debug_url: str, timeout_sec: float, out_path: Path, origin: Optional[str]) -> int:
    cdp_mod = _cdp()
    tab = cdp_mod._find_page_tab(debug_url, "bereza-exchange.com", prefer_url=TARGET_URL)
    if tab is None:
        try:
            tab = cdp_mod._create_new_tab(debug_url, TARGET_URL)
        except urllib.error.HTTPError as exc:
            print("нет вкладки Chromium: HTTP %s" % exc.code, file=sys.stderr)
            return 1
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print("вкладка без webSocketDebuggerUrl", file=sys.stderr)
        return 1
    cdp = cdp_mod.CDPSession(websocket_url=ws_url, timeout_sec=timeout_sec, origin=origin)
    try:
        cdp.call("Page.enable", timeout_sec=timeout_sec)
        cdp.call("Network.enable", timeout_sec=timeout_sec)
        current = tab.get("url") or ""
        if TARGET_URL.rstrip("/") not in current.rstrip("/"):
            cdp.call("Page.navigate", {"url": TARGET_URL}, timeout_sec=timeout_sec)
        title = _wait_past_challenge(cdp, timeout_sec)
        if not title or "just a moment" in title.lower():
            print(
                "Cloudflare challenge не пройден (title=%r). Файл cookie не перезаписан."
                % title,
                file=sys.stderr,
            )
            return 2
        raw = cdp.call(
            "Network.getCookies",
            {"urls": [TARGET_URL, "https://bereza-exchange.com/api/access-token"]},
            timeout_sec=timeout_sec,
        )
        clearance = clearance_from_cookies(raw.get("cookies"))
        if not clearance:
            print("cf_clearance нет в cookie профиля. Файл не перезаписан.", file=sys.stderr)
            return 2
        ua = _user_agent(cdp, timeout_sec=10.0)
        _save(
            out_path,
            {
                "cf_clearance": clearance,
                "user_agent": ua,
                "saved_unix": time.time(),
            },
        )
        print("bereza cf saved len=%d file=%s" % (len(clearance), out_path))
        return 0
    finally:
        cdp.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export Bereza cf_clearance from the Chromium profile into .rates_cache."
    )
    parser.add_argument("--debug-url", default=DEFAULT_DEBUG_URL)
    parser.add_argument("--timeout-ms", type=int, default=45000)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--origin", default=None)
    parser.add_argument("--start-browser", action="store_true")
    parser.add_argument("--chromium-binary", default="chromium-browser")
    parser.add_argument(
        "--user-data-dir",
        default=os.environ.get("BEREZA_CHROMIUM_USER_DATA_DIR", str(Path.home() / ".config" / "chromium")),
    )
    parser.add_argument(
        "--profile-directory",
        default=os.environ.get("BEREZA_CHROMIUM_PROFILE_DIRECTORY", "Default"),
    )
    parser.add_argument("--display", default=":1")
    parser.add_argument("--start-url", default=TARGET_URL)
    parser.add_argument("--browser-ready-timeout", type=float, default=30.0)
    args = parser.parse_args()

    cdp_mod = _cdp()
    browser_proc = None
    try:
        if args.start_browser:
            browser_proc = cdp_mod._start_chromium_browser(
                chromium_binary=args.chromium_binary,
                display=args.display,
                debug_url=args.debug_url,
                start_url=args.start_url,
                user_data_dir=Path(args.user_data_dir).expanduser(),
                profile_directory=(args.profile_directory or "Default").strip() or "Default",
            )
            try:
                cdp_mod._wait_cdp_ready(args.debug_url, timeout_sec=args.browser_ready_timeout)
            except TimeoutError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return run(
            debug_url=args.debug_url,
            timeout_sec=max(1.0, args.timeout_ms / 1000.0),
            out_path=Path(args.out),
            origin=args.origin,
        )
    finally:
        if browser_proc is not None:
            cdp_mod._stop_browser_process(browser_proc)


if __name__ == "__main__":
    raise SystemExit(main())
