#!/usr/bin/env python3
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from websocket._exceptions import WebSocketTimeoutException
except ImportError:  # pragma: no cover
    try:
        from websocket import WebSocketTimeoutException
    except ImportError:  # pragma: no cover
        WebSocketTimeoutException = None  # type: ignore

try:
    from websocket import create_connection
except Exception as exc:  # pragma: no cover
    print(
        "Missing dependency: websocket-client. Install it with:\n"
        "  python3.9 -m pip install websocket-client",
        file=sys.stderr,
    )
    raise SystemExit(3) from exc


TARGET_URL = "https://multitransfer.ru"
DEFAULT_DEBUG_URL = "http://127.0.0.1:9222"
DEFAULT_HEADERS_FILE = Path(__file__).resolve().parents[1] / ".rates_cache" / "multitransfer_headers.json"


def _is_countries_request(url: str) -> bool:
    u = (url or "").lower()
    return "multitransfer-directions" in u and "countries" in u


def _countries_headers_complete(headers: Dict[str, Any]) -> bool:
    return all(
        headers.get(k)
        for k in ("fhprequestid", "fhpsessionid", "x-request-id")
    )


def _lower_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if not headers:
        return out
    for k, v in headers.items():
        out[str(k).lower()] = v
    return out


def _save_headers(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _http_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "cdp-raw-client"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _join(base: str, path: str) -> str:
    return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _debug_port_from_url(debug_url: str) -> int:
    parsed = urllib.parse.urlparse(debug_url)
    if parsed.port is not None:
        return int(parsed.port)
    return 9222


def _wait_cdp_ready(debug_url: str, timeout_sec: float) -> None:
    deadline = time.monotonic() + timeout_sec
    last_exc: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            _http_json(_join(debug_url, "/json/version"))
            return
        except Exception as exc:
            last_exc = exc
            time.sleep(0.2)
    raise TimeoutError(
        f"CDP not ready at {debug_url} within {timeout_sec}s: {last_exc}"
    )


def _start_chromium_browser(
    *,
    chromium_binary: str,
    display: str,
    debug_url: str,
    start_url: str,
) -> subprocess.Popen:
    port = _debug_port_from_url(debug_url)
    env = os.environ.copy()
    env["DISPLAY"] = display
    cmd = [
        chromium_binary,
        "--window-size=1920,1080",
        "--window-position=0,0",
        f"--remote-debugging-port={port}",
        start_url,
    ]
    return subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_browser_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except ProcessLookupError:
            return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _find_existing_tab(debug_url: str, marker: str) -> Optional[Dict[str, Any]]:
    tabs = _http_json(_join(debug_url, "/json/list"))
    marker_lower = marker.lower()
    for tab in tabs:
        if tab.get("type") != "page":
            continue
        if marker_lower in (tab.get("url") or "").lower():
            return tab
    return None


def _create_new_tab(debug_url: str, target_url: str) -> Dict[str, Any]:
    encoded = urllib.parse.quote(target_url, safe=":/?&=#")
    tab = _http_json(_join(debug_url, f"/json/new?{encoded}"))
    if not isinstance(tab, dict):
        raise RuntimeError("Unexpected /json/new response")
    return tab


class CDPSession:
    def __init__(self, websocket_url: str, timeout_sec: float, origin: Optional[str]) -> None:
        conn_kwargs: Dict[str, Any] = {"timeout": timeout_sec}
        if origin is None:
            # Chromium may reject websocket handshakes by Origin. Omitting Origin
            # often works without requiring --remote-allow-origins Chromium flag.
            conn_kwargs["suppress_origin"] = True
        else:
            conn_kwargs["origin"] = origin
        self.ws = create_connection(websocket_url, **conn_kwargs)
        self._next_id = 1
        self._events = []

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, method: str, params: Optional[Dict[str, Any]] = None, timeout_sec: float = 30.0) -> Dict[str, Any]:
        msg_id = self._next_id
        self._next_id += 1
        payload: Dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        self.ws.send(json.dumps(payload, ensure_ascii=False))

        deadline = time.monotonic() + timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Timeout waiting response for {method}")
            self.ws.settimeout(remaining)
            raw = self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == msg_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP error for {method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self._events.append(msg)

    def pop_event(self) -> Optional[Dict[str, Any]]:
        if self._events:
            return self._events.pop(0)
        return None

    def recv_event(self, timeout_sec: float) -> Dict[str, Any]:
        ev = self.pop_event()
        if ev is not None:
            return ev
        if timeout_sec <= 0:
            return {}
        self.ws.settimeout(timeout_sec)
        try:
            raw = self.ws.recv()
        except socket.timeout:
            return {}
        except TimeoutError:
            return {}
        except Exception as exc:
            if WebSocketTimeoutException is not None and isinstance(
                exc, WebSocketTimeoutException
            ):
                return {}
            if type(exc).__name__ == "WebSocketTimeoutException":
                return {}
            raise
        msg = json.loads(raw)
        if "method" not in msg:
            return {}
        return msg


def run(
    debug_url: str,
    marker: str,
    timeout_ms: int,
    keep_open: bool,
    origin: Optional[str],
    countries_settle_ms: int,
    save_headers_file: Optional[Path],
) -> int:
    timeout_sec = max(1.0, timeout_ms / 1000.0)
    try:
        tab = _find_existing_tab(debug_url, marker)
        action = "reload"
        if tab is None:
            tab = _create_new_tab(debug_url, TARGET_URL)
            action = "open"

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Target does not have webSocketDebuggerUrl")

        cdp = CDPSession(websocket_url=ws_url, timeout_sec=timeout_sec, origin=origin)
    except urllib.error.URLError as exc:
        print(
            f"Cannot reach Chromium debug endpoint {debug_url}: {exc}",
            file=sys.stderr,
        )
        return 1
    except Exception as exc:
        print(f"Failed to initialize CDP session: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    try:
        cdp.call("Network.enable", timeout_sec=timeout_sec)
        cdp.call("Page.enable", timeout_sec=timeout_sec)

        if action == "reload":
            cdp.call("Page.reload", {"ignoreCache": True}, timeout_sec=timeout_sec)
        else:
            cdp.call("Page.navigate", {"url": TARGET_URL}, timeout_sec=timeout_sec)

        deadline = time.monotonic() + timeout_sec
        settle_sec = max(0.0, countries_settle_ms / 1000.0)

        latest_countries_get_id: Optional[str] = None
        countries_url_by_id: Dict[str, str] = {}
        base_headers_by_id: Dict[str, Dict[str, Any]] = {}
        extra_headers_by_id: Dict[str, Dict[str, Any]] = {}

        pending_emit_mono: Optional[float] = None
        pending_emit_id: Optional[str] = None
        pending_emit_out: Optional[Dict[str, Any]] = None

        def _merge_for(rid: str) -> Dict[str, Any]:
            merged: Dict[str, Any] = {}
            merged.update(base_headers_by_id.get(rid, {}))
            merged.update(extra_headers_by_id.get(rid, {}))
            return merged

        def _build_out(rid: str, merged: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "action": action,
                "page_url": tab.get("url"),
                "countries_url": countries_url_by_id.get(rid, ""),
                "fhprequestid": merged.get("fhprequestid"),
                "fhpsessionid": merged.get("fhpsessionid"),
                "x-request-id": merged.get("x-request-id"),
            }

        def _schedule_emit(rid: str, merged: Dict[str, Any]) -> None:
            nonlocal pending_emit_mono, pending_emit_id, pending_emit_out
            pending_emit_id = rid
            pending_emit_out = _build_out(rid, merged)
            pending_emit_mono = time.monotonic() + settle_sec

        def _try_emit_pending() -> Optional[int]:
            nonlocal pending_emit_mono, pending_emit_id, pending_emit_out
            if pending_emit_mono is None:
                return None
            if time.monotonic() < pending_emit_mono:
                return None
            if pending_emit_id != latest_countries_get_id or pending_emit_out is None:
                pending_emit_mono = None
                pending_emit_id = None
                pending_emit_out = None
                return None
            print(json.dumps(pending_emit_out, ensure_ascii=False, indent=2))
            if save_headers_file is not None:
                _save_headers(save_headers_file, pending_emit_out)
            if keep_open:
                print("Keep-open mode enabled. Press Ctrl+C to exit.")
                while True:
                    time.sleep(1)
            return 0

        def _maybe_schedule_for_latest() -> None:
            if not latest_countries_get_id:
                return
            rid = latest_countries_get_id
            merged = _merge_for(rid)
            if _countries_headers_complete(merged):
                _schedule_emit(rid, merged)

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                if latest_countries_get_id:
                    merged = _merge_for(latest_countries_get_id)
                    if _countries_headers_complete(merged):
                        out = _build_out(latest_countries_get_id, merged)
                        print(json.dumps(out, ensure_ascii=False, indent=2))
                        if save_headers_file is not None:
                            _save_headers(save_headers_file, out)
                        if keep_open:
                            print("Keep-open mode enabled. Press Ctrl+C to exit.")
                            while True:
                                time.sleep(1)
                        return 0
                print(
                    "Timed out waiting countries request. "
                    "Check page load and network activity in target tab.",
                    file=sys.stderr,
                )
                return 2

            done = _try_emit_pending()
            if done is not None:
                return done

            recv_timeout = min(1.0, remaining)
            if pending_emit_mono is not None:
                until_emit = pending_emit_mono - now
                if until_emit > 0:
                    recv_timeout = min(recv_timeout, until_emit)
                else:
                    recv_timeout = min(recv_timeout, 0.05)

            event = cdp.recv_event(timeout_sec=max(0.05, recv_timeout))
            if not event:
                continue

            method = event.get("method")
            params = event.get("params", {})

            if method == "Network.requestWillBeSentExtraInfo":
                req_id_raw = params.get("requestId")
                if req_id_raw:
                    rid = str(req_id_raw)
                    extra_headers_by_id[rid] = _lower_headers(params.get("headers"))
                    if rid == latest_countries_get_id:
                        _maybe_schedule_for_latest()
                continue

            if method != "Network.requestWillBeSent":
                continue

            request = params.get("request", {})
            req_url = request.get("url", "")
            if not _is_countries_request(req_url):
                continue
            req_method = str(request.get("method") or "").upper()
            if req_method != "GET":
                # Skip preflight OPTIONS; required headers are on real GET request.
                continue

            rid = str(params.get("requestId") or "")
            if not rid:
                continue

            latest_countries_get_id = rid
            countries_url_by_id[rid] = req_url
            base_headers_by_id[rid] = _lower_headers(request.get("headers"))
            pending_emit_mono = None
            pending_emit_id = None
            pending_emit_out = None

            _maybe_schedule_for_latest()
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"CDP runtime error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        cdp.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture multitransfer countries headers via raw CDP (no Playwright)."
    )
    parser.add_argument(
        "--debug-url",
        default=DEFAULT_DEBUG_URL,
        help=f"Chromium remote debugging endpoint (default: {DEFAULT_DEBUG_URL})",
    )
    parser.add_argument(
        "--marker",
        default="multitransfer",
        help="Substring used to find an existing tab URL (default: multitransfer)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Timeout waiting countries request in milliseconds (default: 45000)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Do not exit after capture; keep process alive until Ctrl+C",
    )
    parser.add_argument(
        "--origin",
        default=None,
        help=(
            "Origin header for CDP websocket handshake. "
            "By default Origin is suppressed."
        ),
    )
    parser.add_argument(
        "--start-browser",
        action="store_true",
        help=(
            "Start chromium-browser with DISPLAY, window geometry, "
            "--remote-debugging-port (from --debug-url), then open start URL; "
            "terminate that browser when the script exits."
        ),
    )
    parser.add_argument(
        "--chromium-binary",
        default="chromium-browser",
        help="Browser executable when using --start-browser (default: chromium-browser)",
    )
    parser.add_argument(
        "--display",
        default=":1",
        help="DISPLAY for --start-browser (default: :1)",
    )
    parser.add_argument(
        "--start-url",
        default=TARGET_URL,
        help=f"Initial URL for --start-browser (default: {TARGET_URL})",
    )
    parser.add_argument(
        "--browser-ready-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for CDP after --start-browser (default: 30)",
    )
    parser.add_argument(
        "--countries-settle-ms",
        type=int,
        default=400,
        help=(
            "After the last GET to countries, wait this long for another request "
            "before printing headers (default: 400). Use 0 to emit as soon as headers are complete."
        ),
    )
    parser.add_argument(
        "--save-headers-file",
        default=str(DEFAULT_HEADERS_FILE),
        help=f"Where to persist captured headers for rates.py multitransfer (default: {DEFAULT_HEADERS_FILE})",
    )
    parser.add_argument(
        "--no-save-headers",
        action="store_true",
        help="Do not write captured headers to file",
    )
    args = parser.parse_args()
    save_headers_file = None if args.no_save_headers else Path(args.save_headers_file)

    browser_proc: Optional[subprocess.Popen] = None
    try:
        if args.start_browser:
            browser_proc = _start_chromium_browser(
                chromium_binary=args.chromium_binary,
                display=args.display,
                debug_url=args.debug_url,
                start_url=args.start_url,
            )
            try:
                _wait_cdp_ready(args.debug_url, timeout_sec=args.browser_ready_timeout)
            except TimeoutError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return run(
            debug_url=args.debug_url,
            marker=args.marker,
            timeout_ms=args.timeout_ms,
            keep_open=args.keep_open,
            origin=args.origin,
            countries_settle_ms=args.countries_settle_ms,
            save_headers_file=save_headers_file,
        )
    finally:
        if browser_proc is not None:
            _stop_browser_process(browser_proc)


if __name__ == "__main__":
    raise SystemExit(main())
