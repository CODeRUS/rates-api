# -*- coding: utf-8 -*-
"""Запросы к OpenAI Chat Completions: CLI (--gpt в rates.py) и бот."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Tuple


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


def chat_completion(user_prompt: str) -> Tuple[int, str]:
    """
    Один запрос к Chat Completions.

    Возвращает ``(код_выхода, текст)``: при успехе ``0`` и ответ ассистента; иначе ненулевой
    код и сообщение об ошибке (без печати в stderr).

    Окружение: ``OPENAI_API_KEY``, ``OPENAI_API_URL``, опц. ``OPENAI_PROMPT``, ``OPENAI_MODEL``.
    """
    api_key, url, _env_prompt, model = _config()
    if not api_key:
        return 2, "Задайте OPENAI_API_KEY в окружении."
    if not url:
        return (
            2,
            "Задайте OPENAI_API_URL (полный URL Chat Completions).",
        )

    err, _msg, messages = _messages(user_prompt)
    if err:
        return err, _msg

    payload: Dict[str, Any] = {"model": model, "messages": messages}
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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


def run_openai_gpt(cli_prompt: str) -> int:
    """CLI: печать ответа в stdout, код выхода как у процесса."""
    code, text = chat_completion(cli_prompt)
    if code != 0:
        print(text, file=sys.stderr)
        return code
    print(text, end="" if text.endswith("\n") else "\n")
    return 0
