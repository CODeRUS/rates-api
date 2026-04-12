#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimal OpenRouter proxy with fallback models from config."""
from __future__ import annotations

import json
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")
logger = logging.getLogger("openrouter-proxy")


def _load_config() -> Dict[str, Any]:
    p = Path(os.environ.get("OPENROUTER_PROXY_CONFIG", str(DEFAULT_CONFIG_PATH)))
    raw = p.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("openrouter config must be a JSON object")
    return data


def _json_response(h: BaseHTTPRequestHandler, code: int, payload: Dict[str, Any]) -> None:
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h.send_response(code)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(blob)))
    h.end_headers()
    h.wfile.write(blob)


class Handler(BaseHTTPRequestHandler):
    server_version = "openrouter-proxy/1.0"
    protocol_version = "HTTP/1.1"
    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        # Redirect default BaseHTTPRequestHandler logs to std logging.
        logger.info("%s - " + fmt, self.client_address[0], *args)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            _json_response(self, 200, {"ok": True})
            return
        _json_response(self, 404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            _json_response(self, 404, {"error": "unsupported path"})
            return

        api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
        if not api_key:
            _json_response(self, 500, {"error": "OPENROUTER_API_KEY is not set"})
            return

        n = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(n)
        try:
            incoming = json.loads(body.decode("utf-8"))
        except Exception:
            _json_response(self, 400, {"error": "invalid json"})
            return
        if not isinstance(incoming, dict):
            _json_response(self, 400, {"error": "json must be object"})
            return

        try:
            cfg = _load_config()
        except Exception as e:  # pragma: no cover
            _json_response(self, 500, {"error": f"config error: {e}"})
            return

        # Apply fallback routing from local config.
        outgoing = dict(incoming)
        if "models" in cfg:
            outgoing["models"] = cfg["models"]
        if "route" in cfg:
            outgoing["route"] = cfg["route"]
        # model can conflict with models/route fallback; keep only fallback tuple.
        outgoing.pop("model", None)
        is_stream = bool(outgoing.get("stream"))
        msgs = outgoing.get("messages")
        msg_count = len(msgs) if isinstance(msgs, list) else 0
        logger.info(
            "request in path=%s msgs=%d stream=%s route=%s models=%s",
            self.path,
            msg_count,
            is_stream,
            outgoing.get("route"),
            outgoing.get("models"),
        )

        data = json.dumps(outgoing, ensure_ascii=False).encode("utf-8")
        req = Request(OPENROUTER_URL, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "text/event-stream" if is_stream else "application/json")

        # Optional OpenRouter attribution headers.
        ref = (os.environ.get("OPENROUTER_HTTP_REFERER") or "").strip()
        title = (os.environ.get("OPENROUTER_X_TITLE") or "").strip()
        if ref:
            req.add_header("HTTP-Referer", ref)
        if title:
            req.add_header("X-Title", title)

        timeout = float(os.environ.get("OPENROUTER_PROXY_TIMEOUT_SEC", "120"))

        t0 = time.perf_counter()
        try:
            with urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                ctype = resp.headers.get("Content-Type", "application/json")
                if is_stream:
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "keep-alive")
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()

                    streamed = 0
                    while True:
                        chunk = resp.read(4096)
                        if not chunk:
                            break
                        streamed += len(chunk)
                        self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                        self.wfile.write(chunk)
                        self.wfile.write(b"\r\n")
                        self.wfile.flush()
                    self.wfile.write(b"0\r\n\r\n")
                    self.wfile.flush()
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    logger.info(
                        "request stream_ok status=%s bytes=%d elapsed_ms=%d",
                        status,
                        streamed,
                        elapsed_ms,
                    )
                else:
                    raw = resp.read()
                    elapsed_ms = int((time.perf_counter() - t0) * 1000)
                    logger.info("request ok status=%s elapsed_ms=%d", status, elapsed_ms)
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
        except HTTPError as e:
            payload = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("request http_error status=%s elapsed_ms=%d body=%s", e.code, elapsed_ms, payload[:500])
            _json_response(self, e.code, {"error": "openrouter_http_error", "details": payload})
        except URLError as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.warning("request upstream_unreachable elapsed_ms=%d error=%s", elapsed_ms, e)
            _json_response(self, 502, {"error": "openrouter_unreachable", "details": str(e)})


def main() -> None:
    logging.basicConfig(
        level=getattr(logging, (os.environ.get("OPENROUTER_PROXY_LOG_LEVEL", "INFO").upper()), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    host = os.environ.get("OPENROUTER_PROXY_HOST", "0.0.0.0")
    port = int(os.environ.get("OPENROUTER_PROXY_PORT", "18790"))
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"OpenRouter proxy listening on http://{host}:{port}")
    httpd.serve_forever()


if __name__ == "__main__":
    main()
