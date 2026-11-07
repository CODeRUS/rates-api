#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Снять JSON exchange_prices с калькулятора senateexchange.com (Chromium CDP).

Tilda дергает excenge.store/api/exchange_prices — тот же ответ, что ждёт sources.senate.

Cron (пример)::

    45 * * * * cd /home/coderus/rates-api && DISPLAY=:1 /usr/bin/python3.9 browser/senate_prices_cdp.py --start-browser
"""
from __future__ import annotations

import argparse
import base64
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_ROOT = Path(__file__).resolve().parents[1]
_CDP_PATH = Path(__file__).resolve().parent / "multitransfer_cdp_raw.py"
TARGET_URL = "https://senateexchange.com/"
DEFAULT_DEBUG_URL = "http://127.0.0.1:9226"
SNAP_CHROMIUM = "/snap/bin/chromium"
DEFAULT_USER_DATA_DIR = Path.home() / "snap" / "chromium" / "common" / "chromium-senate"


def _cdp_mod():
    spec = importlib.util.spec_from_file_location("multitransfer_cdp_raw", str(_CDP_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("не удалось загрузить browser/multitransfer_cdp_raw.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_exchange_prices_body(raw: str) -> Dict[str, Any]:
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from sources import senate

    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("Senate: неожиданный JSON %s" % type(data).__name__)
    senate.validate_prices_payload(data)
    return data


def _response_body(cdp, request_id: str) -> Optional[str]:
    try:
        result = cdp.call("Network.getResponseBody", {"requestId": request_id}, timeout_sec=15.0)
    except RuntimeError:
        return None
    body = result.get("body")
    if not isinstance(body, str):
        return None
    if result.get("base64Encoded"):
        try:
            return base64.b64decode(body).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return None
    return body


def capture_exchange_prices(
    cdp,
    *,
    timeout_sec: float,
) -> Dict[str, Any]:
    cdp.call("Network.enable", {})
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        ev = cdp.recv_event(min(1.0, max(0.1, deadline - time.monotonic())))
        if not ev or ev.get("method") != "Network.responseReceived":
            continue
        params = ev.get("params") or {}
        response = params.get("response") or {}
        url = str(response.get("url") or "")
        status = int(response.get("status") or 0)
        if "exchange_prices" not in url or status < 200 or status >= 300:
            continue
        req_id = params.get("requestId")
        if not isinstance(req_id, str):
            continue
        raw = _response_body(cdp, req_id)
        if not raw:
            continue
        try:
            return _parse_exchange_prices_body(raw)
        except (json.JSONDecodeError, RuntimeError):
            continue
    raise TimeoutError(
        "Senate: не перехвачен exchange_prices за %.0fs (проверьте DNS excenge.store)"
        % timeout_sec
    )


def _page_tab(cdp_mod, debug_url: str) -> Dict[str, Any]:
    tabs = cdp_mod._list_page_tabs(debug_url)
    for tab in tabs:
        if isinstance(tab, dict) and tab.get("type") in (None, "page"):
            return tab
    raise RuntimeError("Senate CDP: нет вкладки page")


def run(*, debug_url: str, timeout_sec: float, target_url: str) -> Dict[str, Any]:
    cdp_mod = _cdp_mod()
    tab = _page_tab(cdp_mod, debug_url)
    ws = tab.get("webSocketDebuggerUrl")
    if not isinstance(ws, str):
        raise RuntimeError("Senate CDP: нет webSocketDebuggerUrl")
    cdp = cdp_mod.CDPSession(ws, timeout_sec=30.0, origin=None)
    try:
        cdp.call("Page.enable", {})
        current = str(tab.get("url") or "")
        if target_url not in current:
            cdp.call("Page.navigate", {"url": target_url}, timeout_sec=60.0)
        return capture_exchange_prices(cdp, timeout_sec=timeout_sec)
    finally:
        cdp.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture Senate exchange_prices from senateexchange.com into .rates_cache."
    )
    parser.add_argument("--debug-url", default=DEFAULT_DEBUG_URL)
    parser.add_argument("--timeout-sec", type=float, default=70.0)
    parser.add_argument("--start-url", default=TARGET_URL)
    parser.add_argument("--start-browser", action="store_true")
    parser.add_argument("--chromium-binary", default=os.environ.get("BEREZA_CHROMIUM_BINARY", SNAP_CHROMIUM))
    parser.add_argument("--user-data-dir", default=str(DEFAULT_USER_DATA_DIR))
    parser.add_argument("--profile-directory", default="Default")
    parser.add_argument("--display", default=":1")
    parser.add_argument("--browser-ready-timeout", type=float, default=30.0)
    args = parser.parse_args()

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from sources import senate

    cdp_mod = _cdp_mod()
    browser_proc = None
    debug_url = args.debug_url
    try:
        if args.start_browser:
            user_data = Path(args.user_data_dir).expanduser()
            print("starting %s" % args.chromium_binary, file=sys.stderr)
            browser_proc = cdp_mod._start_chromium_browser(
                chromium_binary=args.chromium_binary,
                display=args.display,
                debug_url=debug_url,
                start_url=args.start_url,
                user_data_dir=user_data,
                profile_directory=(args.profile_directory or "Default").strip() or "Default",
            )
            cdp_mod._wait_cdp_ready(debug_url, timeout_sec=args.browser_ready_timeout)
        data = run(
            debug_url=debug_url,
            timeout_sec=args.timeout_sec,
            target_url=args.start_url,
        )
        senate.save_prices_cache(data)
        rub = senate._price_field(data, "rub_for_usdt_buy")
        thb = senate._price_field(data, "thb_for_usdt_sell")
        print(
            "senate prices saved rub_for_usdt_buy=%s thb_for_usdt_sell=%s file=%s"
            % (rub, thb, senate._cache_file())
        )
        return 0
    except Exception as exc:
        print("senate prices capture failed:", exc, file=sys.stderr)
        return 1
    finally:
        if browser_proc is not None:
            cdp_mod._stop_browser_process(browser_proc)


if __name__ == "__main__":
    raise SystemExit(main())
