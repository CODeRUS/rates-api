#!/usr/bin/env python3
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


TARGET_URL = "https://multitransfer.ru"
DEFAULT_DEBUG_URL = "http://127.0.0.1:9222"
DEFAULT_HEADERS_FILE = Path(__file__).resolve().parents[1] / ".rates_cache" / "multitransfer_headers.json"


def _save_headers(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_countries_request(url: str) -> bool:
    lowered = url.lower()
    return "countries" in lowered and "multitransfer-directions" in lowered


def _find_multitransfer_page(context, marker: str):
    marker_lower = marker.lower()
    for page in context.pages:
        url = (page.url or "").lower()
        if marker_lower in url:
            return page
    return None


def _chromium_launch_args(
    *,
    chromium_arg: list[str] | None,
    window_size: str | None,
    window_position: str | None,
) -> list[str]:
    """Build Chromium CLI flags for p.chromium.launch(args=...)."""
    out: list[str] = []
    if window_size:
        out.append(f"--window-size={window_size.strip()}")
    if window_position:
        out.append(f"--window-position={window_position.strip()}")
    if chromium_arg:
        out.extend(chromium_arg)
    return out


def run(
    use_existing_browser: bool,
    headed: bool,
    keep_open: bool,
    close_delay_ms: int,
    debug_url: str,
    marker: str,
    timeout_ms: int,
    chromium_arg: list[str] | None,
    window_size: str | None,
    window_position: str | None,
    save_headers_file: Path | None,
) -> int:
    delay_before_close = False
    with sync_playwright() as p:
        if use_existing_browser:
            browser = p.chromium.connect_over_cdp(debug_url)
        else:
            launch_kwargs: dict = {"headless": not headed}
            extra = _chromium_launch_args(
                chromium_arg=chromium_arg,
                window_size=window_size,
                window_position=window_position,
            )
            if extra:
                launch_kwargs["args"] = extra
            browser = p.chromium.launch(**launch_kwargs)
        try:
            if use_existing_browser and browser.contexts:
                context = browser.contexts[0]
            else:
                context = browser.new_context()

            page = _find_multitransfer_page(context, marker)
            action = "reload"
            if page is None:
                page = context.new_page()
                action = "open"

            with page.expect_request(
                lambda req: _is_countries_request(req.url),
                timeout=timeout_ms,
            ) as req_info:
                if action == "reload":
                    page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                else:
                    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=timeout_ms)

            request = req_info.value
            headers = request.headers
            result = {
                "action": action,
                "page_url": page.url,
                "countries_url": request.url,
                "fhprequestid": headers.get("fhprequestid"),
                "fhpsessionid": headers.get("fhpsessionid"),
                "x-request-id": headers.get("x-request-id"),
            }
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if save_headers_file is not None:
                _save_headers(save_headers_file, result)
            if keep_open:
                print("Keep-open mode enabled. Browser will stay open until Ctrl+C.")
                while True:
                    time.sleep(1)
            if close_delay_ms > 0:
                delay_before_close = True
            return 0
        except PlaywrightTimeoutError:
            if use_existing_browser:
                print(
                    "Timed out waiting for countries request. "
                    "Ensure Chromium is running with --remote-debugging-port=9222 "
                    "and the page can load multitransfer.",
                    file=sys.stderr,
                )
            else:
                print(
                    "Timed out waiting for countries request in local launched Chromium.",
                    file=sys.stderr,
                )
            return 2
        except Exception as exc:
            print(f"Failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        finally:
            if delay_before_close:
                time.sleep(close_delay_ms / 1000.0)
            browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Capture countries headers either from an existing Chromium CDP session "
            "or from a temporary launched Chromium."
        )
    )
    parser.add_argument(
        "--use-existing-browser",
        action="store_true",
        help="Use already running Chromium via --debug-url (CDP)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch local Chromium with visible window (ignored with --use-existing-browser)",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Do not close browser automatically; keep it open until Ctrl+C",
    )
    parser.add_argument(
        "--close-delay-ms",
        type=int,
        default=0,
        help=(
            "After a successful capture, wait this many milliseconds before closing "
            "the browser (ignored with --keep-open; default: 0)"
        ),
    )
    parser.add_argument(
        "--debug-url",
        default=DEFAULT_DEBUG_URL,
        help=f"CDP endpoint URL for --use-existing-browser (default: {DEFAULT_DEBUG_URL})",
    )
    parser.add_argument(
        "--marker",
        default="multitransfer",
        help="Substring to identify an existing tab URL (default: multitransfer)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=45000,
        help="Timeout in milliseconds (default: 45000)",
    )
    parser.add_argument(
        "--chromium-arg",
        action="append",
        dest="chromium_arg",
        metavar="ARG",
        help=(
            "Extra Chromium launch flag (repeatable), e.g. "
            '--chromium-arg="--disable-extensions"'
        ),
    )
    parser.add_argument(
        "--window-size",
        metavar="WxH",
        help='Shortcut for --window-size=W,H (e.g. 1920,1080)',
    )
    parser.add_argument(
        "--window-position",
        metavar="X,Y",
        help='Shortcut for --window-position=X,Y (e.g. 0,0)',
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
    return run(
        use_existing_browser=args.use_existing_browser,
        headed=args.headed,
        keep_open=args.keep_open,
        close_delay_ms=args.close_delay_ms,
        debug_url=args.debug_url,
        marker=args.marker,
        timeout_ms=args.timeout_ms,
        chromium_arg=args.chromium_arg,
        window_size=args.window_size,
        window_position=args.window_position,
        save_headers_file=save_headers_file,
    )


if __name__ == "__main__":
    raise SystemExit(main())
