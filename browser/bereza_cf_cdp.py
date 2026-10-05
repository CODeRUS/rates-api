#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Снять cf_clearance Bereza из уже пройденного Chromium-профиля и записать
``.rates_cache/bereza_cf.json`` для источника ``bereza``.

Запуск snap Chromium (профиль ``~/snap/chromium/common/chromium``, DISPLAY :1)::

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
SNAP_CHROMIUM = "/snap/bin/chromium"
DEFAULT_USER_DATA_DIR = Path.home() / "snap" / "chromium" / "common" / "chromium"


def default_chromium_binary(path: Optional[str] = None) -> str:
    """Только snap Chromium. Системный chromium-browser не подставлять."""
    candidate = path or SNAP_CHROMIUM
    if os.path.exists(candidate):
        return candidate
    raise FileNotFoundError(candidate)


def _cdp():
    spec = importlib.util.spec_from_file_location("multitransfer_cdp_raw", str(_CDP_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError("не удалось загрузить browser/multitransfer_cdp_raw.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def find_bereza_tab(tabs: Any, marker: str = "bereza-exchange.com") -> Optional[Dict[str, Any]]:
    """Только вкладка Bereza. Чужую вкладку не подменять."""
    if not isinstance(tabs, list):
        return None
    marker_l = marker.lower()
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        if tab.get("type") not in (None, "page"):
            continue
        url = str(tab.get("url") or "")
        if marker_l in url.lower():
            return tab
    return None


def live_chromium_pid(user_data_dir: Path) -> Optional[int]:
    lock = user_data_dir / "SingletonLock"
    if not lock.exists() and not lock.is_symlink():
        return None
    try:
        target = os.readlink(str(lock))
    except OSError:
        return None
    suffix = target.rsplit("-", 1)[-1]
    try:
        pid = int(suffix)
    except ValueError:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    except OSError:
        return None
    return pid


def debug_url_from_cmdline(argv: Any) -> Optional[str]:
    if isinstance(argv, bytes):
        text = argv.replace(b"\0", b" ").decode("utf-8", "replace")
    elif isinstance(argv, str):
        text = argv
    else:
        chunks = []
        for part in argv:
            chunks.append(part.decode("utf-8", "replace") if isinstance(part, bytes) else str(part))
        text = " ".join(chunks)
    marker = "--remote-debugging-port="
    start = text.find(marker)
    if start < 0:
        return None
    port = []
    for ch in text[start + len(marker) :]:
        if ch.isdigit():
            port.append(ch)
        else:
            break
    if not port:
        return None
    return "http://127.0.0.1:%s" % "".join(port)


def debug_url_of_pid(pid: int) -> Optional[str]:
    try:
        raw = Path("/proc/%d/cmdline" % pid).read_bytes()
    except OSError:
        return None
    return debug_url_from_cmdline(raw)


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


def is_cloudflare_stub(title: str) -> bool:
    """Заглушка Cloudflare, а не страница обменника."""
    low = (title or "").strip().lower()
    if not low:
        return False
    return (
        "just a moment" in low
        or "performing security verification" in low
        or "checking your browser" in low
        or "attention required" in low
        or low.startswith("cloudflare")
    )


def clearance_usable(title: str, http_status: int, clearance: Optional[str]) -> bool:
    """Кука пригодна только после живой загрузки, не по заголовку из кэша."""
    if not title or is_cloudflare_stub(title):
        return False
    if http_status != 200:
        return False
    return isinstance(clearance, str) and bool(clearance.strip())


def attributes_to_dict(attributes: Any) -> Dict[str, str]:
    if not isinstance(attributes, list):
        return {}
    out: Dict[str, str] = {}
    items = iter(attributes)
    for key in items:
        try:
            value = next(items)
        except StopIteration:
            break
        out[str(key)] = "" if value is None else str(value)
    return out


def is_turnstile_iframe(node_name: str, attributes: Any) -> bool:
    if str(node_name or "").upper() != "IFRAME":
        return False
    attrs = attributes_to_dict(attributes)
    src = (attrs.get("src") or "").lower()
    title = (attrs.get("title") or "").lower()
    return "challenges.cloudflare.com" in src or "turnstile" in src or "turnstile" in title or "cloudflare" in title


def checkbox_click_point(content: Any) -> Optional[tuple]:
    """Левая галочка виджета. content — квад content из DOM.getBoxModel."""
    if not isinstance(content, (list, tuple)) or len(content) < 8:
        return None
    try:
        xs = [float(content[i]) for i in range(0, 8, 2)]
        ys = [float(content[i]) for i in range(1, 8, 2)]
    except (TypeError, ValueError):
        return None
    x1 = min(xs)
    y1 = min(ys)
    width = max(xs) - x1
    height = max(ys) - y1
    if width < 40 or height < 20:
        return None
    return (x1 + 28.0, y1 + height / 2.0)


def _eval_json(cdp, expression: str, timeout_sec: float) -> Dict[str, Any]:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expression, "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    value = (result.get("result") or {}).get("value")
    if not isinstance(value, str):
        return {}
    try:
        data = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _user_agent(cdp, timeout_sec: float) -> str:
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": "navigator.userAgent || ''", "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    value = (result.get("result") or {}).get("value")
    return value.strip() if isinstance(value, str) else ""


def _wait_loaded(cdp, timeout_sec: float) -> str:
    """Дождаться уже открытой страницы. Без reload и без новой навигации."""
    deadline = time.monotonic() + timeout_sec
    title = ""
    expr = "JSON.stringify({title: document.title || '', ready: document.readyState || ''})"
    while time.monotonic() < deadline:
        state = _eval_json(cdp, expr, timeout_sec=10.0)
        title = str(state.get("title") or "")
        ready = str(state.get("ready") or "")
        if ready == "complete" and title and not is_cloudflare_stub(title):
            return title
        time.sleep(0.5)
    return title


def _wait_ready(cdp, timeout_sec: float) -> str:
    """Дождаться complete, в том числе на заглушке. Без reload."""
    deadline = time.monotonic() + timeout_sec
    title = ""
    expr = "JSON.stringify({title: document.title || '', ready: document.readyState || ''})"
    while time.monotonic() < deadline:
        state = _eval_json(cdp, expr, timeout_sec=10.0)
        title = str(state.get("title") or "")
        ready = str(state.get("ready") or "")
        if ready == "complete" and title:
            return title
        time.sleep(0.4)
    return title


def _scroll_offset(cdp, timeout_sec: float) -> tuple:
    state = _eval_json(
        cdp,
        "JSON.stringify({x: window.scrollX || 0, y: window.scrollY || 0})",
        timeout_sec=timeout_sec,
    )
    try:
        return float(state.get("x") or 0), float(state.get("y") or 0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _turnstile_click_point(cdp, timeout_sec: float) -> Optional[tuple]:
    try:
        cdp.call("DOM.getDocument", {"depth": 0, "pierce": True}, timeout_sec=timeout_sec)
        found = cdp.call(
            "DOM.performSearch",
            {"query": "iframe", "includeUserAgentShadowDOM": True},
            timeout_sec=timeout_sec,
        )
    except RuntimeError:
        return None
    search_id = found.get("searchId") if isinstance(found, dict) else None
    try:
        count = int((found or {}).get("resultCount") or 0)
    except (TypeError, ValueError):
        count = 0
    try:
        if not search_id or count <= 0:
            return None
        result = cdp.call(
            "DOM.getSearchResults",
            {"searchId": search_id, "fromIndex": 0, "toIndex": min(count, 40)},
            timeout_sec=timeout_sec,
        )
        scroll_x, scroll_y = _scroll_offset(cdp, timeout_sec=min(5.0, timeout_sec))
        for node_id in result.get("nodeIds") or []:
            desc = cdp.call("DOM.describeNode", {"nodeId": node_id}, timeout_sec=timeout_sec)
            node = desc.get("node") or {}
            if not is_turnstile_iframe(node.get("nodeName"), node.get("attributes")):
                continue
            try:
                box = cdp.call("DOM.getBoxModel", {"nodeId": node_id}, timeout_sec=timeout_sec)
            except RuntimeError:
                continue
            point = checkbox_click_point((box.get("model") or {}).get("content"))
            if point is None:
                continue
            x = point[0] - scroll_x
            y = point[1] - scroll_y
            if x < 0 or y < 0 or x > 4000 or y > 3000:
                continue
            return (x, y)
        return None
    finally:
        if search_id:
            try:
                cdp.call("DOM.discardSearchResults", {"searchId": search_id}, timeout_sec=5.0)
            except RuntimeError:
                pass


def _dispatch_click(cdp, x: float, y: float, timeout_sec: float) -> None:
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseMoved", "x": x, "y": y},
        timeout_sec=timeout_sec,
    )
    time.sleep(0.05)
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1},
        timeout_sec=timeout_sec,
    )
    time.sleep(0.05)
    cdp.call(
        "Input.dispatchMouseEvent",
        {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1},
        timeout_sec=timeout_sec,
    )


def click_turnstile_checkbox(cdp, timeout_sec: float) -> bool:
    """Клик по галочке Turnstile, если виджет уже на странице."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        point = _turnstile_click_point(cdp, timeout_sec=min(10.0, max(1.0, deadline - time.monotonic())))
        if point is not None:
            _dispatch_click(cdp, point[0], point[1], timeout_sec=10.0)
            return True
        time.sleep(0.4)
    return False


def _read_title_and_status(cdp, timeout_sec: float) -> tuple:
    title = _wait_loaded(cdp, timeout_sec)
    status = 0
    if title and not is_cloudflare_stub(title):
        status = _probe_access_token(cdp, timeout_sec)
    return title, status


def _pass_cloudflare_stub(cdp, title: str, status: int, timeout_sec: float) -> tuple:
    """Если открыта заглушка — кликнуть галочку и дождаться сайта. Иначе не трогать страницу."""
    if title and not is_cloudflare_stub(title) and status == 200:
        return title, status
    for _attempt in range(2):
        if title and not is_cloudflare_stub(title) and status == 200:
            break
        # На заглушке виджет появляется не сразу. Без заглушки — один короткий поиск.
        wait = 12.0 if is_cloudflare_stub(title) else 2.0
        clicked = click_turnstile_checkbox(cdp, timeout_sec=min(wait, timeout_sec))
        if not clicked:
            if is_cloudflare_stub(title):
                print("cloudflare: галочка не найдена", file=sys.stderr)
            break
        print("cloudflare: клик по галочке", file=sys.stderr)
        title, status = _read_title_and_status(cdp, timeout_sec=min(20.0, timeout_sec))
    return title, status


def _probe_access_token(cdp, timeout_sec: float) -> int:
    expr = (
        "(async () => {"
        " const r = await fetch('/api/access-token', {credentials:'include', headers:{accept:'*/*'}});"
        " return String(r.status);"
        "})()"
    )
    result = cdp.call(
        "Runtime.evaluate",
        {"expression": expr, "awaitPromise": True, "returnByValue": True},
        timeout_sec=timeout_sec,
    )
    value = (result.get("result") or {}).get("value")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _save(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(str(tmp), 0o600)
    tmp.replace(path)


def _wait_bereza_tab(cdp_mod, debug_url: str, timeout_sec: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout_sec
    while True:
        tab = find_bereza_tab(cdp_mod._list_page_tabs(debug_url))
        if tab is not None:
            return tab
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.4)


def run(
    debug_url: str,
    timeout_sec: float,
    out_path: Path,
    origin: Optional[str],
    target_url: str = TARGET_URL,
) -> int:
    cdp_mod = _cdp()
    tab = _wait_bereza_tab(cdp_mod, debug_url, timeout_sec=5.0)
    if tab is None:
        print("открываю вкладку %s" % target_url)
        try:
            tab = cdp_mod._create_new_tab(debug_url, target_url)
        except urllib.error.HTTPError as exc:
            print("нет вкладки Chromium: HTTP %s" % exc.code, file=sys.stderr)
            return 1
    else:
        print("вкладка уже открыта: %s" % (tab.get("url") or target_url))
    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        print("вкладка без webSocketDebuggerUrl", file=sys.stderr)
        return 1
    cdp = cdp_mod.CDPSession(websocket_url=ws_url, timeout_sec=timeout_sec, origin=origin)
    try:
        cdp.call("Page.enable", timeout_sec=timeout_sec)
        cdp.call("Network.enable", timeout_sec=timeout_sec)
        title = _wait_ready(cdp, min(8.0, timeout_sec))
        status = _probe_access_token(cdp, timeout_sec) if title and not is_cloudflare_stub(title) else 0
        title, status = _pass_cloudflare_stub(cdp, title, status, timeout_sec)
        raw = cdp.call(
            "Network.getCookies",
            {"urls": [TARGET_URL, "https://bereza-exchange.com/api/access-token"]},
            timeout_sec=timeout_sec,
        )
        clearance = clearance_from_cookies(raw.get("cookies"))
        if not clearance_usable(title, status, clearance):
            print(
                "Bereza cookie не обновлена (title=%r status=%s). Файл не перезаписан."
                % (title, status),
                file=sys.stderr,
            )
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
        print("bereza cf saved len=%d status=%s file=%s" % (len(clearance), status, out_path))
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
    parser.add_argument(
        "--chromium-binary",
        default=os.environ.get("BEREZA_CHROMIUM_BINARY", default_chromium_binary()),
    )
    parser.add_argument(
        "--user-data-dir",
        default=os.environ.get("BEREZA_CHROMIUM_USER_DATA_DIR", str(DEFAULT_USER_DATA_DIR)),
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
    debug_url = args.debug_url
    try:
        if args.start_browser:
            user_data = Path(args.user_data_dir).expanduser()
            pid = live_chromium_pid(user_data)
            if pid:
                exe = cdp_mod._proc_exe(pid)
                if not cdp_mod.is_snap_chromium_exe(exe):
                    print("уже запущен не snap chromium: %s" % exe, file=sys.stderr)
                    return 1
                existing = debug_url_of_pid(pid)
                if not existing:
                    print(
                        "Chromium уже запущен без remote debugging, второй экземпляр не открываю.",
                        file=sys.stderr,
                    )
                    return 1
                debug_url = existing
                print("браузер уже запущен: %s exe %s" % (debug_url, exe))
            else:
                # Без URL: вкладки восстанавливает сессия. Новую откроем ниже, если Bereza нет.
                print("starting %s" % args.chromium_binary, file=sys.stderr)
                browser_proc = cdp_mod._start_chromium_browser(
                    chromium_binary=args.chromium_binary,
                    display=args.display,
                    debug_url=debug_url,
                    start_url="",
                    user_data_dir=user_data,
                    profile_directory=(args.profile_directory or "Default").strip() or "Default",
                )
                try:
                    cdp_mod._wait_cdp_ready(debug_url, timeout_sec=args.browser_ready_timeout)
                except TimeoutError as exc:
                    print(str(exc), file=sys.stderr)
                    return 1
                ok, detail = cdp_mod.cdp_owner_ok(
                    cdp_mod._debug_port_from_url(debug_url),
                    browser_proc.pid,
                    user_data,
                )
                if not ok:
                    print(detail, file=sys.stderr)
                    return 1
                print("chromium exe %s" % detail, file=sys.stderr)
        return run(
            debug_url=debug_url,
            timeout_sec=max(1.0, args.timeout_ms / 1000.0),
            out_path=Path(args.out),
            origin=args.origin,
            target_url=args.start_url or TARGET_URL,
        )
    finally:
        if browser_proc is not None:
            cdp_mod._stop_browser_process(browser_proc)


if __name__ == "__main__":
    raise SystemExit(main())
