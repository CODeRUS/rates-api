# -*- coding: utf-8 -*-
"""Запросы к OpenAI Chat Completions: CLI (--gpt в rates.py) и бот."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple


_DEFAULT_OPENAI_HTTP_TIMEOUT_SEC = 300.0


def http_timeout_sec() -> float:
    """
    Таймаут одного HTTP-запроса к Chat Completions (сек).

    Переменная окружения: ``OPENAI_HTTP_TIMEOUT_SEC`` (по умолчанию 300).
    """
    raw = (os.environ.get("OPENAI_HTTP_TIMEOUT_SEC") or "").strip()
    if not raw:
        return _DEFAULT_OPENAI_HTTP_TIMEOUT_SEC
    try:
        v = float(raw)
        return v if v >= 30.0 else _DEFAULT_OPENAI_HTTP_TIMEOUT_SEC
    except ValueError:
        return _DEFAULT_OPENAI_HTTP_TIMEOUT_SEC


def _config() -> tuple[str, str, str, str]:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    url = (os.environ.get("OPENAI_API_URL") or "").strip()
    env_prompt = (os.environ.get("OPENAI_PROMPT") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()
    return api_key, url, env_prompt, model


def _messages(user_text: str) -> tuple[int, str, List[Dict[str, str]]]:
    user_text = (user_text or "").strip()
    _, _, env_prompt, _ = _config()
    messages: List[Dict[str, str]] = []
    if env_prompt:
        messages.append({"role": "system", "content": env_prompt})
    if user_text:
        messages.append({"role": "user", "content": user_text})
    if not messages:
        return (
            2,
            "Пустой запрос: укажите текст или OPENAI_PROMPT в .env.",
            [],
        )
    return 0, "", messages


def _payload_user_field(user_id: Optional[str]) -> Optional[str]:
    """OpenAI Chat Completions: поле ``user`` (до 64 симв.), стабильный id конечного пользователя."""
    if user_id is not None:
        raw = user_id.strip()
    else:
        raw = (os.environ.get("OPENAI_GPT_USER") or "").strip()
    if not raw:
        return None
    full = f"rates-client-{raw}"
    return full[:64]


def _base_payload(
    user_prompt: str, *, user_id: Optional[str]
) -> Tuple[int, str, Optional[Dict[str, Any]]]:
    api_key, url, _env_prompt, model = _config()
    if not api_key:
        return 2, "Задайте OPENAI_API_KEY в окружении.", None
    if not url:
        return (
            2,
            "Задайте OPENAI_API_URL (полный URL Chat Completions).",
            None,
        )
    err, _msg, messages = _messages(user_prompt)
    if err:
        return err, _msg, None

    payload: Dict[str, Any] = {"model": model, "messages": messages}
    user_field = _payload_user_field(user_id)
    if user_field:
        payload["user"] = user_field
    return 0, "", payload


def chat_completion(user_prompt: str, *, user_id: Optional[str] = None) -> Tuple[int, str]:
    """
    Один запрос к Chat Completions.

    Возвращает ``(код_выхода, текст)``: при успехе ``0`` и ответ ассистента; иначе ненулевой
    код и сообщение об ошибке (без печати в stderr).

    Окружение: ``OPENAI_API_KEY``, ``OPENAI_API_URL``, опц. ``OPENAI_PROMPT``, ``OPENAI_MODEL``,
    ``OPENAI_GPT_USER``, ``OPENAI_HTTP_TIMEOUT_SEC`` (таймаут HTTP, по умолчанию 300 с).

    ``user_id`` переопределяет суффикс для вызова из бота (Telegram user id и т.п.).
    """
    api_key, url, _env_prompt, _model = _config()
    err, msg, payload = _base_payload(user_prompt, user_id=user_id)
    if err:
        return err, msg
    assert payload is not None
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.strip(),
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=http_timeout_sec()) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return 1, f"HTTP {e.code}: {body or e.reason}"
    except urllib.error.URLError as e:
        return 1, f"Сеть: {e.reason}"
    except OSError as e:
        return 1, str(e)

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return 1, raw[:4000] if raw else "Некорректный JSON в ответе."

    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return 1, json.dumps(obj, ensure_ascii=False, indent=2)[:4000]

    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = (msg or {}).get("content") if isinstance(msg, dict) else None
    if not isinstance(content, str):
        return 1, json.dumps(obj, ensure_ascii=False, indent=2)[:4000]

    return 0, content


def stream_chat_completion(
    user_prompt: str,
    *,
    user_id: Optional[str] = None,
    on_delta: Optional[Callable[[str], None]] = None,
) -> Tuple[int, str]:
    """Потоковый Chat Completions (SSE): возвращает полный текст, on_delta вызывается по кускам."""
    api_key, url, _env_prompt, _model = _config()
    err, msg, payload = _base_payload(user_prompt, user_id=user_id)
    if err:
        return err, msg
    assert payload is not None
    payload["stream"] = True
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url.strip(),
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    parts: List[str] = []
    try:
        with urllib.request.urlopen(req, timeout=http_timeout_sec()) as resp:
            for bline in resp:
                line = bline.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                first = choices[0] if isinstance(choices[0], dict) else {}
                delta = first.get("delta") if isinstance(first, dict) else {}
                piece = None
                if isinstance(delta, dict):
                    piece = delta.get("content")
                if not isinstance(piece, str):
                    msg_obj = first.get("message") if isinstance(first, dict) else {}
                    if isinstance(msg_obj, dict):
                        piece = msg_obj.get("content")
                if isinstance(piece, str) and piece:
                    parts.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return 1, f"HTTP {e.code}: {body or e.reason}"
    except urllib.error.URLError as e:
        return 1, f"Сеть: {e.reason}"
    except OSError as e:
        return 1, str(e)
    return 0, "".join(parts)


def run_openai_gpt(cli_prompt: str) -> int:
    """CLI: печать ответа в stdout, код выхода как у процесса."""
    code, text = stream_chat_completion(
        cli_prompt, on_delta=lambda s: print(s, end="", flush=True)
    )
    if code != 0:
        print(text, file=sys.stderr)
        return code
    if text and not text.endswith("\n"):
        print()
    return 0
