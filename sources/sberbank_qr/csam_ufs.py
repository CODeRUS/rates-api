# -*- coding: utf-8 -*-
"""
Обновление UFS-кук через CSAM-цепочку (при HTTP 403 на sberpayUE).
Все адреса и чувствительные параметры задаются через окружение (см. SBER_QR_CSAM_*).
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, Mapping, MutableMapping, Tuple

from rates_http import urlopen_retriable


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


def parse_csam_login_xml(xml_text: str) -> Tuple[str, str]:
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
) -> Tuple[str, Dict[str, str]]:
    """
    1) POST CSAM login (x-www-form-urlencoded)
    2) POST aggregate session/create (JSON)

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

    login_headers = {
        "user-agent": ua,
        "referer": referer,
        "content-type": "application/x-www-form-urlencoded",
    }
    req_login = urllib.request.Request(
        login_url,
        data=body_raw.encode("utf-8"),
        method="POST",
        headers=login_headers,
    )
    with urlopen_retriable(req_login, timeout=timeout_sec, context=ctx) as resp:
        login_bytes = resp.read()
    host, token = parse_csam_login_xml(_decode_xml_bytes(login_bytes))

    agg_url = agg_url_tmpl.format(host=host)
    agg_payload = {
        "platform": "android",
        "token": token,
        "version": app_ver,
    }
    agg_body = json.dumps(agg_payload, ensure_ascii=False).encode("utf-8")
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
        _ = resp.read()
        cookies = ufs_cookies_from_response_headers(resp.headers)
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
    # Дополнительно объединяем env mapping с os.environ:
    # внешние вызовы могут передать частичный env без всех SBER_QR_CSAM_*.
    merged_env = dict(os.environ)
    merged_env.update(dict(env))
    host, fresh = fetch_ufs_cookies_via_csam(
        merged_env,
        verify_ssl=verify_ssl,
        timeout_sec=timeout_sec,
    )
    cookies.clear()
    cookies.update(fresh)
    return host
