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


def _is_commissions_request(url: str) -> bool:
    u = (url or "").lower()
    return "multitransfer-fee-calc" in u and "commissions" in u


def _headers_complete(headers: Dict[str, Any]) -> bool:
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


def _http_opener() -> urllib.request.OpenerDirector:
    # Do not route localhost CDP through HTTP_PROXY (often causes 405/connection errors).
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(url: str, method: str = "GET") -> Any:
    req = urllib.request.Request(
        url,
        method=method.upper(),
        headers={"User-Agent": "cdp-raw-client"},
    )
    with _http_opener().open(req, timeout=10) as resp:
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
        chromium_binary, "--guest",
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


def _list_page_tabs(debug_url: str) -> list:
    tabs = _http_json(_join(debug_url, "/json/list"))
    if not isinstance(tabs, list):
        raise RuntimeError("Unexpected /json/list response")
    return [t for t in tabs if isinstance(t, dict) and t.get("type") == "page"]


def _find_page_tab(debug_url: str, marker: str) -> Optional[Dict[str, Any]]:
    """
    Prefer a page tab whose URL contains marker; otherwise use the first page tab
    (e.g. chrome://newtab/) and navigate to TARGET_URL via CDP.
    """
    pages = _list_page_tabs(debug_url)
    marker_lower = marker.lower()
    for tab in pages:
        if marker_lower in (tab.get("url") or "").lower():
            return tab
    return pages[0] if pages else None


def _create_new_tab(debug_url: str, target_url: str) -> Dict[str, Any]:
    encoded = urllib.parse.quote(target_url, safe=":/?&=#")
    # Chromium 111+ rejects GET /json/new (405); creation requires PUT.
    try:
        tab = _http_json(_join(debug_url, f"/json/new?{encoded}"), method="PUT")
    except urllib.error.HTTPError as exc:
        if exc.code == 405:
            tab = _http_json(_join(debug_url, f"/json/new?{encoded}"), method="GET")
        else:
            raise
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


_FIELD_RECT_JS = """
(() => {
  const inp = document.querySelector('input[name="amount"]');
  if (!inp) return null;
  inp.scrollIntoView({block: 'center', inline: 'nearest'});
  const r = inp.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return null;
  return {x: r.x + r.width / 2, y: r.y + r.height / 2};
})()
"""

_FIELD_VALUE_JS = """
(() => {
  const inp = document.querySelector('input[name="amount"]');
  return inp ? {
    value: inp.value || '',
    active: document.activeElement === inp,
  } : null;
})()
"""


def _eval_value(cdp: "CDPSession", expression: str, timeout_sec: float) -> Any:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(f"CDP evaluate failed: {result['exceptionDetails']}")
    return result.get("result", {}).get("value")


def _wait_amount_field_center(cdp: "CDPSession", timeout_sec: float) -> Dict[str, float]:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        rect = _eval_value(cdp, _FIELD_RECT_JS, timeout_sec=min(5.0, timeout_sec))
        if isinstance(rect, dict) and rect.get("x") is not None and rect.get("y") is not None:
            return {"x": float(rect["x"]), "y": float(rect["y"])}
        time.sleep(0.25)
    raise RuntimeError("Timeout waiting for input[name=amount] field")


def _mouse_click(cdp: "CDPSession", x: float, y: float, *, click_count: int = 1) -> None:
    for event_type in ("mousePressed", "mouseReleased"):
        cdp.call(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": click_count,
            },
            timeout_sec=10.0,
        )


def _key_down_up(
    cdp: "CDPSession",
    *,
    key: str,
    code: str,
    vk: int,
    modifiers: int = 0,
) -> None:
    for event_type in ("keyDown", "keyUp"):
        params: Dict[str, Any] = {
            "type": event_type,
            "key": key,
            "code": code,
            "windowsVirtualKeyCode": vk,
        }
        if modifiers:
            params["modifiers"] = modifiers
        cdp.call("Input.dispatchKeyEvent", params, timeout_sec=10.0)


_COMMIT_AMOUNT_JS = """
((digits) => {
  const inp = document.querySelector('input[name="amount"]');
  if (!inp) return {ok: false, reason: 'no input[name=amount]'};
  const desc = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value');
  if (desc && desc.set) desc.set.call(inp, '');
  else inp.value = '';
  inp.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContentBackward'}));
  if (desc && desc.set) desc.set.call(inp, digits);
  else inp.value = digits;
  inp.dispatchEvent(new InputEvent('input', {bubbles: true, data: digits, inputType: 'insertText'}));
  inp.dispatchEvent(new Event('change', {bubbles: true}));
  inp.dispatchEvent(new FocusEvent('blur', {bubbles: true}));
  return {ok: true, value: inp.value, digits: digits};
})
"""


def _commit_amount_value(cdp: "CDPSession", digits: str, timeout_sec: float) -> Dict[str, Any]:
    expr = _COMMIT_AMOUNT_JS.strip() + f"({json.dumps(digits)})"
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expr, "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    if result.get("exceptionDetails"):
        raise RuntimeError(f"CDP evaluate failed: {result['exceptionDetails']}")
    value = result.get("result", {}).get("value")
    if not isinstance(value, dict) or not value.get("ok"):
        reason = value.get("reason") if isinstance(value, dict) else "unknown"
        raise RuntimeError(f"Failed to commit amount: {reason}")
    return value


def _type_digit(cdp: "CDPSession", ch: str) -> None:
    vk = ord(ch)
    cdp.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyDown",
            "key": ch,
            "code": f"Digit{ch}",
            "windowsVirtualKeyCode": vk,
        },
        timeout_sec=10.0,
    )
    cdp.call("Input.dispatchKeyEvent", {"type": "char", "text": ch}, timeout_sec=10.0)
    cdp.call(
        "Input.dispatchKeyEvent",
        {
            "type": "keyUp",
            "key": ch,
            "code": f"Digit{ch}",
            "windowsVirtualKeyCode": vk,
        },
        timeout_sec=10.0,
    )


def _enter_amount(cdp: "CDPSession", amount: str, timeout_sec: float) -> Dict[str, Any]:
    """Click field for focus/caret, type digits via keyboard, commit for React/API."""
    try:
        cdp.call("Page.bringToFront", timeout_sec=5.0)
    except Exception:
        pass

    center = _wait_amount_field_center(cdp, timeout_sec=min(timeout_sec, 25.0))
    x, y = center["x"], center["y"]

    _mouse_click(cdp, x, y, click_count=1)
    time.sleep(0.3)

    state = _eval_value(cdp, _FIELD_VALUE_JS, timeout_sec=5.0)
    if not isinstance(state, dict):
        raise RuntimeError("Amount field not found after click")
    if not state.get("active"):
        # Second click if MUI did not focus on first click.
        _mouse_click(cdp, x, y, click_count=1)
        time.sleep(0.2)

    digits = "".join(ch for ch in str(amount) if ch.isdigit())
    if not digits:
        raise RuntimeError(f"Invalid --amount: {amount!r}")

    # Clear existing text like a user (select all + delete).
    _key_down_up(cdp, key="a", code="KeyA", vk=65, modifiers=2)
    time.sleep(0.05)
    _key_down_up(cdp, key="Backspace", code="Backspace", vk=8)
    time.sleep(0.1)

    for ch in digits:
        _type_digit(cdp, ch)
        time.sleep(0.07)

    time.sleep(0.15)
    # MUI/React listens to native value updates; keyboard alone does not trigger commissions.
    committed = _commit_amount_value(cdp, digits, timeout_sec=10.0)
    return {"ok": True, "value": committed.get("value"), "digits": digits}


def run(
    debug_url: str,
    marker: str,
    timeout_ms: int,
    keep_open: bool,
    origin: Optional[str],
    settle_ms: int,
    amount: str,
    save_headers_file: Optional[Path],
) -> int:
    timeout_sec = max(1.0, timeout_ms / 1000.0)
    try:
        tab = _find_page_tab(debug_url, marker)
        if tab is None:
            try:
                tab = _create_new_tab(debug_url, TARGET_URL)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    "No page tabs in Chromium and cannot create one via /json/new "
                    f"(HTTP {exc.code}). Open any page in the browser or pass --start-browser."
                ) from exc
            action = "open"
            navigate = False
        else:
            tab_url = (tab.get("url") or "").lower()
            marker_lower = marker.lower()
            if marker_lower in tab_url or TARGET_URL.lower().rstrip("/") in tab_url.rstrip("/"):
                # Already on transfer page; reload often prevents commissions after amount input.
                action = "use-existing"
                navigate = False
            else:
                action = "navigate"
                navigate = True

        ws_url = tab.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Target does not have webSocketDebuggerUrl")

        cdp = CDPSession(websocket_url=ws_url, timeout_sec=timeout_sec, origin=origin)
    except urllib.error.HTTPError as exc:
        print(
            f"Chromium debug HTTP {exc.code} for {exc.url}: {exc.reason}. "
            "Check --debug-url and that Chromium was started with --remote-debugging-port.",
            file=sys.stderr,
        )
        return 1
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
        cdp.call("Runtime.enable", timeout_sec=timeout_sec)

        if navigate:
            if action == "open":
                pass  # /json/new already opened TARGET_URL
            else:
                cdp.call("Page.navigate", {"url": TARGET_URL}, timeout_sec=timeout_sec)
            # Wait for transfer form; flsafety must finish before commissions fires.
            try:
                _wait_amount_field_center(cdp, timeout_sec=min(25.0, timeout_sec * 0.6))
            except RuntimeError:
                pass
            settle_until = time.monotonic() + 3.0
            while time.monotonic() < settle_until:
                cdp.recv_event(timeout_sec=0.2)
            page_ready_at = time.monotonic()
        else:
            page_ready_at = time.monotonic() + 1.0

        deadline = time.monotonic() + timeout_sec
        settle_sec = max(0.0, settle_ms / 1000.0)

        latest_commissions_post_id: Optional[str] = None
        commissions_url_by_id: Dict[str, str] = {}
        base_headers_by_id: Dict[str, Dict[str, Any]] = {}
        extra_headers_by_id: Dict[str, Dict[str, Any]] = {}
        shared_fhp_headers: Dict[str, Any] = {}
        amount_entered = False

        pending_emit_mono: Optional[float] = None
        pending_emit_id: Optional[str] = None
        pending_emit_out: Optional[Dict[str, Any]] = None

        def _merge_for(rid: str) -> Dict[str, Any]:
            merged: Dict[str, Any] = {}
            merged.update(base_headers_by_id.get(rid, {}))
            merged.update(extra_headers_by_id.get(rid, {}))
            return merged

        def _merge_best_headers(rid: str) -> Dict[str, Any]:
            merged = _merge_for(rid)
            for key in ("fhprequestid", "fhpsessionid", "x-request-id"):
                if not merged.get(key) and shared_fhp_headers.get(key):
                    merged[key] = shared_fhp_headers[key]
            if _headers_complete(merged):
                return merged
            for other_rid in set(base_headers_by_id) | set(extra_headers_by_id):
                if other_rid == rid:
                    continue
                candidate = _merge_for(other_rid)
                for key in ("fhprequestid", "fhpsessionid", "x-request-id"):
                    if not merged.get(key) and candidate.get(key):
                        merged[key] = candidate[key]
            return merged

        def _remember_fhp(headers: Dict[str, Any]) -> None:
            for key in ("fhprequestid", "fhpsessionid", "x-request-id"):
                if headers.get(key):
                    shared_fhp_headers[key] = headers[key]

        def _build_out(rid: str, merged: Dict[str, Any]) -> Dict[str, Any]:
            return {
                "action": action,
                "page_url": tab.get("url"),
                "commissions_url": commissions_url_by_id.get(rid, ""),
                "amount": amount,
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
            if pending_emit_id != latest_commissions_post_id or pending_emit_out is None:
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
            if not latest_commissions_post_id:
                return
            rid = latest_commissions_post_id
            merged = _merge_best_headers(rid)
            if _headers_complete(merged):
                _schedule_emit(rid, merged)

        def _handle_network_event(event: Dict[str, Any]) -> None:
            nonlocal latest_commissions_post_id, amount_entered
            nonlocal pending_emit_mono, pending_emit_id, pending_emit_out

            method = event.get("method")
            params = event.get("params", {})

            if method == "Network.requestWillBeSentExtraInfo":
                req_id_raw = params.get("requestId")
                if req_id_raw:
                    rid = str(req_id_raw)
                    lowered = _lower_headers(params.get("headers"))
                    extra_headers_by_id[rid] = lowered
                    _remember_fhp(lowered)
                    if amount_entered and rid == latest_commissions_post_id:
                        _maybe_schedule_for_latest()
                return

            if method != "Network.requestWillBeSent":
                return

            request = params.get("request", {})
            req_url = request.get("url", "")
            rid = str(params.get("requestId") or "")
            if not rid:
                return

            if "api.multitransfer.ru" in (req_url or "").lower():
                lowered = _lower_headers(request.get("headers"))
                base_headers_by_id[rid] = lowered
                _remember_fhp(lowered)

            if not _is_commissions_request(req_url):
                return
            req_method = str(request.get("method") or "").upper()
            if req_method != "POST":
                return
            if not amount_entered:
                return

            latest_commissions_post_id = rid
            commissions_url_by_id[rid] = req_url
            pending_emit_mono = None
            pending_emit_id = None
            pending_emit_out = None
            _maybe_schedule_for_latest()

        def _drain_queued_events() -> None:
            while True:
                ev = cdp.pop_event()
                if not ev:
                    break
                _handle_network_event(ev)

        def _try_enter_amount() -> None:
            nonlocal amount_entered
            if amount_entered:
                return
            if time.monotonic() < page_ready_at:
                return
            remaining = deadline - time.monotonic()
            if remaining < 2.0:
                return
            # Mark before typing so commissions POST during Input.* calls is handled.
            amount_entered = True
            try:
                _enter_amount(cdp, amount, timeout_sec=min(remaining, 35.0))
            except Exception:
                amount_entered = False
                raise
            _drain_queued_events()
            # Commissions may arrive slightly after commit; pump briefly.
            post_deadline = time.monotonic() + 2.0
            while time.monotonic() < post_deadline:
                ev = cdp.recv_event(timeout_sec=0.2)
                if ev:
                    _handle_network_event(ev)
                if latest_commissions_post_id:
                    break

        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                if latest_commissions_post_id:
                    merged = _merge_best_headers(latest_commissions_post_id)
                    if _headers_complete(merged):
                        out = _build_out(latest_commissions_post_id, merged)
                        print(json.dumps(out, ensure_ascii=False, indent=2))
                        if save_headers_file is not None:
                            _save_headers(save_headers_file, out)
                        if keep_open:
                            print("Keep-open mode enabled. Press Ctrl+C to exit.")
                            while True:
                                time.sleep(1)
                        return 0
                if not amount_entered:
                    print(
                        "Timed out: transfer amount field did not appear or amount was not entered.",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "Timed out waiting commissions request after amount input. "
                        "Check page load and network activity in target tab.",
                        file=sys.stderr,
                    )
                return 2

            done = _try_emit_pending()
            if done is not None:
                return done

            _try_enter_amount()

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
            _handle_network_event(event)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"CDP runtime error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        cdp.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture multitransfer anti-bot headers via raw CDP: enter amount on the "
            "transfer form and intercept POST commissions request (no Playwright)."
        )
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
        help="Timeout waiting commissions request in milliseconds (default: 45000)",
    )
    parser.add_argument(
        "--amount",
        default="10000",
        help="Amount to type into «Сумма отправления» (default: 10000)",
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
        "--settle-ms",
        type=int,
        default=400,
        dest="settle_ms",
        help=(
            "After the last POST to commissions, wait this long for another request "
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
            settle_ms=args.settle_ms,
            amount=args.amount,
            save_headers_file=save_headers_file,
        )
    finally:
        if browser_proc is not None:
            _stop_browser_process(browser_proc)


if __name__ == "__main__":
    raise SystemExit(main())
