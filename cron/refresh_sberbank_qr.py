#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import ssl
import sys
import time
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_loader import load_repo_dotenv, patch_repo_dotenv
import rates_unified_cache as ucc
from rates_http import urlopen_retriable

load_repo_dotenv(_ROOT)

_ETERNAL_TTL_SEC = 315360000  # 10 years; cache module treats ttl=0 as expired
_TZ_BANGKOK = ZoneInfo("Asia/Bangkok")


def _short_repr(value: Any, limit: int = 240) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _mask_secret(raw: str, keep: int = 4) -> str:
    value = (raw or "").strip()
    if not value:
        return "<empty>"
    if len(value) <= keep:
        return "*" * len(value)
    return ("*" * max(4, len(value) - keep)) + value[-keep:]


def _log_step(step: str, **fields: Any) -> None:
    parts = ["sberbank_qr", f"step={step}"]
    for key, value in fields.items():
        parts.append(f"{key}={_short_repr(value)}")
    print(" ".join(parts))


def _safe_json_preview(value: Any, limit: int = 800) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except Exception:
        text = repr(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "...<truncated>"


def _masked_cookies(cookies: Dict[str, str]) -> Dict[str, str]:
    return {name: _mask_secret(val) for name, val in cookies.items() if val}


def _payload_debug_fields(payload: Any) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {
            "payload_type": type(payload).__name__,
            "payload_preview": _safe_json_preview(payload),
        }

    body = payload.get("body")
    status = payload.get("status")
    messages = payload.get("messages")
    message_codes: list[str] = []
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict):
                code = str(item.get("code") or "").strip()
                if code:
                    message_codes.append(code)

    fields: Dict[str, Any] = {
        "success": payload.get("success"),
        "top_keys": sorted(payload.keys()),
        "payload_preview": _safe_json_preview(payload),
    }
    if isinstance(body, dict):
        fields.update(
            {
                "flow": body.get("flow"),
                "state": body.get("state"),
                "result": body.get("result"),
            }
        )
    if isinstance(status, dict):
        fields["status_code"] = status.get("code")
        err_ids: list[str] = []
        for err in status.get("errors") or []:
            if isinstance(err, dict):
                err_id = str(err.get("id") or "").strip()
                if err_id:
                    err_ids.append(err_id)
        if err_ids:
            fields["status_error_ids"] = err_ids
    if message_codes:
        fields["message_codes"] = message_codes
    return fields


def _bangkok_now_note() -> str:
    """Строка времени для примитива (отображается в сводке): часовой пояс Бангкок."""
    return datetime.now(_TZ_BANGKOK).strftime("%Y-%m-%d %H:%M %z")


def _unified_cache_path() -> Path:
    raw = (os.environ.get("RATES_UNIFIED_CACHE_FILE") or "").strip()
    if not raw:
        return ucc.DEFAULT_UNIFIED_CACHE_PATH
    p = Path(raw)
    return p if p.is_absolute() else (_ROOT / p).resolve()


@dataclass(frozen=True)
class RequestParams:
    """Параметры запроса Sber QR; endpoint-шаблоны берутся из окружения."""
    api_host: str
    url: str
    session_check_url: str
    session_notification_url: str
    cookies: Dict[str, str]
    payload: Dict[str, Any]
    timeout_sec: float
    verify_ssl: bool


@dataclass(frozen=True)
class ParseResult:
    kind: str  # success | ignore | alert
    exchange_rate: float | None
    reason: str


def parse_sber_qr_response(payload: Any) -> ParseResult:
    if not isinstance(payload, dict):
        return ParseResult("ignore", None, "payload is not dict")

    status = payload.get("status")
    if isinstance(status, dict) and int(status.get("code") or 0) == 3:
        for err in status.get("errors") or []:
            if isinstance(err, dict) and str(err.get("id") or "") == "EFSGW-154":
                return ParseResult("alert", None, "EFSGW-154: bad upstream response")
        return ParseResult("ignore", None, "status code 3 without EFSGW-154")

    body = payload.get("body")
    if isinstance(body, dict):
        flow = str(body.get("flow") or "")
        state = str(body.get("state") or "")
        if flow == "SberPayUERouter" and state == "onEnter":
            return ParseResult("ignore", None, "router onEnter soft failure")
        if flow == "SberPayUEPayment" and state == "mainScreen":
            output = body.get("output")
            if isinstance(output, dict):
                for screen in output.get("screens") or []:
                    if not isinstance(screen, dict):
                        continue
                    for widget in screen.get("widgets") or []:
                        if not isinstance(widget, dict):
                            continue
                        props = widget.get("properties")
                        if not isinstance(props, dict):
                            continue
                        rate = props.get("exchangeRate")
                        try:
                            rate_f = float(rate)
                        except (TypeError, ValueError):
                            continue
                        if rate_f > 0:
                            return ParseResult("success", rate_f, "ok")
            return ParseResult("ignore", None, "payment mainScreen without exchangeRate")

    return ParseResult("ignore", None, "unrecognized payload")


def _normalize_host(raw_host: str) -> str:
    """Нормализует host[:port] без доменных/портовых предположений."""
    host = (raw_host or "").strip()
    if not host:
        return host
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].strip()
    return host


def build_request_params(env: Dict[str, str]) -> RequestParams:
    host_raw = (env.get("SBER_QR_HOSTNAME") or "").strip()
    token = (env.get("SBER_QR_UFS_TOKEN") or "").strip()
    sess = (env.get("SBER_QR_UFS_SESSION") or "").strip()
    link = (env.get("SBER_QR_LINK") or "").strip()
    timeout = float((env.get("SBER_QR_TIMEOUT_SEC") or "25").strip() or "25")
    verify_ssl = (env.get("SBER_QR_VERIFY_SSL") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    host = _normalize_host(host_raw)
    flow_tmpl = (env.get("SBER_QR_FLOW_URL_TEMPLATE") or "").strip()
    check_tmpl = (env.get("SBER_QR_SESSION_CHECK_URL_TEMPLATE") or "").strip()
    notif_tmpl = (env.get("SBER_QR_SESSION_NOTIFICATION_URL_TEMPLATE") or "").strip()
    if not host or not token or not sess or not link:
        raise RuntimeError(
            "SBER_QR_HOSTNAME/SBER_QR_UFS_TOKEN/SBER_QR_UFS_SESSION/SBER_QR_LINK required"
        )
    if not flow_tmpl or not check_tmpl or not notif_tmpl:
        raise RuntimeError(
            "SBER_QR_FLOW_URL_TEMPLATE/SBER_QR_SESSION_CHECK_URL_TEMPLATE/"
            "SBER_QR_SESSION_NOTIFICATION_URL_TEMPLATE required"
        )
    return RequestParams(
        api_host=host,
        url=flow_tmpl.format(host=host),
        session_check_url=check_tmpl.format(host=host),
        session_notification_url=notif_tmpl.format(host=host),
        cookies={"UFS-TOKEN": token, "UFS-SESSION": sess},
        payload={"fields": {"link": link}},
        timeout_sec=timeout,
        verify_ssl=verify_ssl,
    )


def _urls_for_api_host(api_host: str) -> tuple[str, str, str]:
    flow_tmpl = (os.environ.get("SBER_QR_FLOW_URL_TEMPLATE") or "").strip()
    check_tmpl = (os.environ.get("SBER_QR_SESSION_CHECK_URL_TEMPLATE") or "").strip()
    notif_tmpl = (os.environ.get("SBER_QR_SESSION_NOTIFICATION_URL_TEMPLATE") or "").strip()
    if not flow_tmpl or not check_tmpl or not notif_tmpl:
        raise RuntimeError(
            "SBER_QR_FLOW_URL_TEMPLATE/SBER_QR_SESSION_CHECK_URL_TEMPLATE/"
            "SBER_QR_SESSION_NOTIFICATION_URL_TEMPLATE required"
        )
    return (
        flow_tmpl.format(host=api_host),
        check_tmpl.format(host=api_host),
        notif_tmpl.format(host=api_host),
    )


def send_admin_alert(text: str) -> None:
    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    admin_id = (os.environ.get("BOT_ADMIN_ID") or "").strip()
    send_msg_tmpl = (os.environ.get("TELEGRAM_BOT_SEND_MESSAGE_URL_TEMPLATE") or "").strip()
    if not bot_token or not admin_id:
        return
    if not send_msg_tmpl:
        return
    body = json.dumps(
        {"chat_id": int(admin_id), "text": text},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        send_msg_tmpl.format(bot_token=bot_token),
        data=body,
        method="POST",
        headers={"content-type": "application/json; charset=UTF-8"},
    )
    try:
        with urlopen_retriable(
            req,
            timeout=10,
            context=ssl.create_default_context(),
        ) as _:
            pass
    except Exception:
        # alert channel should not break cron
        return


def _cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)


def _ssl_context(verify_ssl: bool) -> ssl.SSLContext:
    if verify_ssl:
        return ssl.create_default_context()
    return ssl._create_unverified_context()


def _decode_xml_bytes(data: bytes) -> str:
    for enc in ("utf-8", "windows-1251", "cp1251", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_csam_login_xml(xml_text: str) -> tuple[str, str]:
    """
    Из ответа CSAM: (host, token) для шага создания aggregate-сессии.
    """
    root = ET.fromstring(xml_text)
    code_el = root.find(".//status/code")
    if code_el is None or (code_el.text or "").strip() != "0":
        snippet = xml_text[:800].replace("\n", " ")
        raise RuntimeError(f"sberbank_qr CSAM: login status code не 0, фрагмент: {snippet!r}")
    host_el = root.find(".//loginData/host")
    tok_el = root.find(".//loginData/token")
    if host_el is None or tok_el is None or not (host_el.text or "").strip():
        raise RuntimeError("sberbank_qr CSAM: в ответе нет loginData/host или token")
    host = (host_el.text or "").strip()
    token = (tok_el.text or "").strip()
    if not token:
        raise RuntimeError("sberbank_qr CSAM: пустой token в loginData")
    return host, token


def ufs_cookies_from_response_headers(headers: Any) -> Dict[str, str]:
    """Читает Set-Cookie и вытаскивает UFS-TOKEN / UFS-SESSION (первое значение до ``;``)."""
    lines: list[str] = []
    if hasattr(headers, "get_all"):
        got = headers.get_all("Set-Cookie")
        if got:
            lines = list(got)
    if not lines:
        one = headers.get("Set-Cookie")
        if one:
            lines = [one]
    out: Dict[str, str] = {}
    for line in lines:
        part = (line or "").split(";", 1)[0].strip()
        if "=" not in part:
            continue
        name, val = part.split("=", 1)
        name = name.strip()
        if name in ("UFS-TOKEN", "UFS-SESSION"):
            out[name] = val.strip()
    if "UFS-TOKEN" not in out or "UFS-SESSION" not in out:
        raise RuntimeError(
            "sberbank_qr CSAM: в ответе aggregate session нет Set-Cookie "
            "UFS-TOKEN/UFS-SESSION"
        )
    return out


def fetch_ufs_cookies_via_csam(
    env: Mapping[str, str],
    *,
    verify_ssl: bool,
    timeout_sec: float,
) -> tuple[str, Dict[str, str]]:
    """
    1) POST CSAM login (x-www-form-urlencoded)
    2) POST aggregate session/create

    Возвращает ``(host, {"UFS-TOKEN":..., "UFS-SESSION":...})`` для последующих запросов к API.
    """
    body_raw = (env.get("SBER_QR_CSAM_LOGIN_BODY") or "").strip()
    if not body_raw:
        raise RuntimeError(
            "sberbank_qr: при HTTP 403 нужен SBER_QR_CSAM_LOGIN_BODY "
            "(тело POST CSAM login, как в curl -d '...')"
        )
    login_url = (env.get("SBER_QR_CSAM_LOGIN_URL") or "").strip()
    if not login_url:
        raise RuntimeError("sberbank_qr: требуется SBER_QR_CSAM_LOGIN_URL")
    agg_url_tmpl = (env.get("SBER_QR_CSAM_AGGREGATE_URL_TEMPLATE") or "").strip()
    if not agg_url_tmpl:
        raise RuntimeError("sberbank_qr: требуется SBER_QR_CSAM_AGGREGATE_URL_TEMPLATE")
    ua = (env.get("SBER_QR_CSAM_USER_AGENT") or "").strip() or "Mobile Device"
    referer = (env.get("SBER_QR_CSAM_REFERER") or "").strip() or "Android/15/17.5.0"
    app_ver = (env.get("SBER_QR_APP_VERSION") or "").strip() or "17.5.0"
    ctx = _ssl_context(verify_ssl)
    _log_step(
        "csam_login_start",
        login_url=login_url,
        verify_ssl=verify_ssl,
        timeout_sec=timeout_sec,
        user_agent=ua,
        referer=referer,
        login_body_len=len(body_raw),
    )

    req_login = urllib.request.Request(
        login_url,
        data=body_raw.encode("utf-8"),
        method="POST",
        headers={
            "user-agent": ua,
            "referer": referer,
            "content-type": "application/x-www-form-urlencoded",
        },
    )
    with urlopen_retriable(req_login, timeout=timeout_sec, context=ctx) as resp:
        login_bytes = resp.read()
    login_xml = _decode_xml_bytes(login_bytes)
    _log_step("csam_login_response", bytes_len=len(login_bytes))
    host, token = parse_csam_login_xml(login_xml)
    _log_step("csam_login_parsed", host=host, token=_mask_secret(token))

    agg_url = agg_url_tmpl.format(host=host)
    agg_body = json.dumps(
        {
            "platform": "android",
            "token": token,
            "version": app_ver,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    _log_step(
        "csam_aggregate_start",
        aggregate_url=agg_url,
        app_version=app_ver,
        token=_mask_secret(token),
    )
    req_agg = urllib.request.Request(
        agg_url,
        data=agg_body,
        method="POST",
        headers={
            "content-type": "application/json; charset=UTF-8",
            "user-agent": ua,
            "referer": referer,
        },
    )
    with urlopen_retriable(req_agg, timeout=timeout_sec, context=ctx) as resp:
        resp_body = resp.read()
        cookies = ufs_cookies_from_response_headers(resp.headers)
    _log_step(
        "csam_aggregate_success",
        bytes_len=len(resp_body),
        cookies=_masked_cookies(cookies),
    )
    return host, cookies


def apply_csam_refresh_to_cookies(
    env: MutableMapping[str, str],
    cookies: MutableMapping[str, str],
    *,
    verify_ssl: bool,
    timeout_sec: float,
) -> str:
    """
    Выполняет CSAM-цепочку, пишет новые UFS-* в ``cookies``.
    Возвращает host (для последующих запросов API).
    """
    merged_env = dict(os.environ)
    merged_env.update(dict(env))
    _log_step("csam_refresh_start", current_cookies=_masked_cookies(dict(cookies)))
    host, fresh = fetch_ufs_cookies_via_csam(
        merged_env,
        verify_ssl=verify_ssl,
        timeout_sec=timeout_sec,
    )
    cookies.clear()
    cookies.update(fresh)
    _log_step("csam_refresh_done", host=host, fresh_cookies=_masked_cookies(dict(cookies)))
    return host


def _post_json(
    *,
    url: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
    payload: Dict[str, Any],
    timeout_sec: float,
    verify_ssl: bool,
) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = dict(headers)
    req_headers["cookie"] = _cookie_header(cookies)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=req_headers,
    )
    with urlopen_retriable(
        req,
        timeout=timeout_sec,
        context=_ssl_context(verify_ssl),
    ) as resp:
        raw = resp.read().decode(
            resp.headers.get_content_charset() or "utf-8",
            errors="replace",
        )
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise RuntimeError("sberbank_qr: invalid json response")
    return data


def _post_text(
    *,
    url: str,
    headers: Dict[str, str],
    cookies: Dict[str, str],
    payload: Dict[str, Any],
    timeout_sec: float,
    verify_ssl: bool,
) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req_headers = dict(headers)
    req_headers["cookie"] = _cookie_header(cookies)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers=req_headers,
    )
    with urlopen_retriable(
        req,
        timeout=timeout_sec,
        context=_ssl_context(verify_ssl),
    ) as resp:
        status = int(getattr(resp, "status", 200))
        raw = resp.read().decode(
            resp.headers.get_content_charset() or "utf-8",
            errors="replace",
        )
    return status, raw


def _skip_dotenv_ufs_write() -> bool:
    return (os.environ.get("SBER_QR_SKIP_DOTENV_WRITE") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _persist_ufs_after_csam(api_host: str, cookies: Dict[str, str]) -> None:
    """Пишет новые UFS-куки и хост ноды в ``.env`` (чтобы следующий запуск не шёл в CSAM)."""
    if _skip_dotenv_ufs_write():
        _log_step("dotenv_skip", reason="SBER_QR_SKIP_DOTENV_WRITE")
        return
    tok = (cookies.get("UFS-TOKEN") or "").strip()
    sess = (cookies.get("UFS-SESSION") or "").strip()
    host = (api_host or "").strip()
    if not tok or not sess or not host:
        _log_step(
            "dotenv_skip",
            reason="missing_data",
            host=host,
            cookies=_masked_cookies(cookies),
        )
        return
    updates = {
        "SBER_QR_UFS_TOKEN": tok,
        "SBER_QR_UFS_SESSION": sess,
        "SBER_QR_HOSTNAME": host,
    }
    if patch_repo_dotenv(_ROOT, updates):
        os.environ["SBER_QR_UFS_TOKEN"] = tok
        os.environ["SBER_QR_UFS_SESSION"] = sess
        os.environ["SBER_QR_HOSTNAME"] = host
        _log_step(
            "dotenv_updated",
            host=host,
            cookies=_masked_cookies(cookies),
        )
    else:
        _log_step("dotenv_update_failed", host=host)


def _env_verify_csam(env: Dict[str, str], fallback: bool) -> bool:
    raw = (env.get("SBER_QR_CSAM_VERIFY_SSL") or "").strip().lower()
    if not raw:
        return fallback
    return raw in {"1", "true", "yes", "on"}


def main() -> int:
    params = build_request_params(os.environ)
    headers = {"content-type": "application/json; charset=UTF-8"}
    cookies: Dict[str, str] = dict(params.cookies)
    api_host = params.api_host
    flow_url, check_url, notif_url = _urls_for_api_host(api_host)
    csam_verify = _env_verify_csam(os.environ, params.verify_ssl)
    dotenv_ufs_pending = False
    _log_step(
        "start",
        api_host=api_host,
        flow_url=flow_url,
        check_url=check_url,
        notif_url=notif_url,
        timeout_sec=params.timeout_sec,
        verify_ssl=params.verify_ssl,
        csam_verify=csam_verify,
        cookies=_masked_cookies(cookies),
        link_len=len(str(params.payload.get("fields", {}).get("link") or "")),
    )

    def _sberpay_start(attempt: int) -> Dict[str, Any]:
        _log_step(
            "flow_request_start",
            attempt=attempt,
            url=flow_url,
            cookies=_masked_cookies(cookies),
            payload_preview=_safe_json_preview(params.payload),
        )
        payload = _post_json(
            url=flow_url,
            headers=headers,
            cookies=cookies,
            payload=params.payload,
            timeout_sec=params.timeout_sec,
            verify_ssl=params.verify_ssl,
        )
        _log_step("flow_response", attempt=attempt, **_payload_debug_fields(payload))
        return payload

    try:
        payload = _sberpay_start(1)
    except urllib.error.HTTPError as e:
        _log_step("flow_request_http_error", code=e.code, reason=str(e))
        if e.code != 403:
            raise
        try:
            e.read()
        except Exception:
            pass
        _log_step("flow_request_retry_via_csam", reason="http_403")
        api_host = apply_csam_refresh_to_cookies(
            os.environ,
            cookies,
            verify_ssl=csam_verify,
            timeout_sec=params.timeout_sec,
        )
        flow_url, check_url, notif_url = _urls_for_api_host(api_host)
        _log_step(
            "flow_request_after_csam",
            api_host=api_host,
            flow_url=flow_url,
            check_url=check_url,
            notif_url=notif_url,
        )
        payload = _sberpay_start(2)
        dotenv_ufs_pending = True

    # Second request is mandatory: we only log response for observability.
    _log_step("session_check_start", url=check_url)
    check_status, check_body = _post_text(
        url=check_url,
        headers=headers,
        cookies=cookies,
        payload={},
        timeout_sec=params.timeout_sec,
        verify_ssl=params.verify_ssl,
    )
    _log_step(
        "session_check_response",
        status=check_status,
        body_preview=check_body[:1000],
    )

    try:
        _log_step("session_notification_start", url=notif_url)
        notif_status, notif_body = _post_text(
            url=notif_url,
            headers=headers,
            cookies=cookies,
            payload={},
            timeout_sec=params.timeout_sec,
            verify_ssl=params.verify_ssl,
        )
        _log_step(
            "session_notification_response",
            status=notif_status,
            body_preview=notif_body[:1000],
        )
    except Exception as e:
        _log_step("session_notification_error", error=repr(e))

    parsed = parse_sber_qr_response(payload)
    _log_step(
        "parse_result",
        kind=parsed.kind,
        reason=parsed.reason,
        exchange_rate=parsed.exchange_rate,
    )
    if parsed.kind == "ignore":
        if dotenv_ufs_pending:
            _persist_ufs_after_csam(api_host, cookies)
        _log_step("finish", result="ignore", reason=parsed.reason)
        return 0
    if parsed.kind == "alert":
        if dotenv_ufs_pending:
            _persist_ufs_after_csam(api_host, cookies)
        send_admin_alert(f"Sberbank QR hard fail: {parsed.reason}")
        _log_step("finish", result="alert", reason=parsed.reason)
        return 0
    assert parsed.exchange_rate is not None
    _log_step("exchange_rate_found", exchange_rate=parsed.exchange_rate)
    cache_path = _unified_cache_path()
    doc = ucc.load_unified(cache_path)
    ucc.prim_set(
        doc,
        "prim:sber_qr_transfer",
        {
            "rate": float(parsed.exchange_rate),
            "label": "Сбербанк QR",
            "note": _bangkok_now_note(),
            "updated_unix": int(time.time()),
        },
        ttl_sec=_ETERNAL_TTL_SEC,
    )
    ucc.save_unified(doc, cache_path)
    _log_step(
        "cache_saved",
        cache_path=str(cache_path),
        key="prim:sber_qr_transfer",
        exchange_rate=parsed.exchange_rate,
    )
    if dotenv_ufs_pending:
        _persist_ufs_after_csam(api_host, cookies)
    _log_step("finish", result="success", exchange_rate=parsed.exchange_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
