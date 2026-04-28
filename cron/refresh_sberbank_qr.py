#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import ssl
import sys
import time
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from env_loader import load_repo_dotenv, patch_repo_dotenv
import rates_unified_cache as ucc
from rates_http import urlopen_retriable
from sources.sberbank_qr.csam_ufs import apply_csam_refresh_to_cookies
from sources.sberbank_qr.parser import parse_sber_qr_response

load_repo_dotenv(_ROOT)

_ETERNAL_TTL_SEC = 315360000  # 10 years; cache module treats ttl=0 as expired
_TZ_BANGKOK = ZoneInfo("Asia/Bangkok")


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
        print("sberbank_qr: SBER_QR_SKIP_DOTENV_WRITE — не обновляю .env")
        return
    tok = (cookies.get("UFS-TOKEN") or "").strip()
    sess = (cookies.get("UFS-SESSION") or "").strip()
    host = (api_host or "").strip()
    if not tok or not sess or not host:
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
        print(
            "sberbank_qr: в .env обновлены SBER_QR_UFS_TOKEN, SBER_QR_UFS_SESSION, "
            "SBER_QR_HOSTNAME"
        )
    else:
        print("sberbank_qr: не удалось записать .env (файл отсутствует или ошибка ОС)")


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

    def _sberpay_start() -> Dict[str, Any]:
        return _post_json(
            url=flow_url,
            headers=headers,
            cookies=cookies,
            payload=params.payload,
            timeout_sec=params.timeout_sec,
            verify_ssl=params.verify_ssl,
        )

    try:
        payload = _sberpay_start()
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise
        try:
            e.read()
        except Exception:
            pass
        print("sberbank_qr: Sber QR flow HTTP 403 — CSAM login + aggregate session/create")
        api_host = apply_csam_refresh_to_cookies(
            os.environ,
            cookies,
            verify_ssl=csam_verify,
            timeout_sec=params.timeout_sec,
        )
        flow_url, check_url, notif_url = _urls_for_api_host(api_host)
        print(f"sberbank_qr: после CSAM api_host={api_host}")
        payload = _sberpay_start()
        dotenv_ufs_pending = True

    # Second request is mandatory: we only log response for observability.
    check_status, check_body = _post_text(
        url=check_url,
        headers=headers,
        cookies=cookies,
        payload={},
        timeout_sec=params.timeout_sec,
        verify_ssl=params.verify_ssl,
    )
    try:
        print(
            f"sber_session_check status={check_status} "
            f"body={check_body[:1000]}"
        )
    except Exception:
        print(f"sber_session_check status={check_status} body=<unreadable>")

    try:
        notif_status, notif_body = _post_text(
            url=notif_url,
            headers=headers,
            cookies=cookies,
            payload={},
            timeout_sec=params.timeout_sec,
            verify_ssl=params.verify_ssl,
        )
        try:
            print(
                f"sber_session_notification status={notif_status} "
                f"body={notif_body[:1000]}"
            )
        except Exception:
            print(
                f"sber_session_notification status={notif_status} body=<unreadable>"
            )
    except Exception as e:
        print(f"sber_session_notification error={e!r}")

    parsed = parse_sber_qr_response(payload)
    if parsed.kind == "ignore":
        if dotenv_ufs_pending:
            _persist_ufs_after_csam(api_host, cookies)
        return 0
    if parsed.kind == "alert":
        if dotenv_ufs_pending:
            _persist_ufs_after_csam(api_host, cookies)
        send_admin_alert(f"Sberbank QR hard fail: {parsed.reason}")
        return 0
    assert parsed.exchange_rate is not None
    print(f"sber_qr_current_rate={parsed.exchange_rate:.6f}")
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
    if dotenv_ufs_pending:
        _persist_ufs_after_csam(api_host, cookies)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
