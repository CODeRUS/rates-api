# -*- coding: utf-8 -*-
"""Один запрос к OpenAI Chat Completions из CLI (--gpt в rates.py)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def run_openai_gpt(cli_prompt: str) -> int:
    """
    Окружение:

    * ``OPENAI_API_KEY`` — токен (Bearer).
    * ``OPENAI_API_URL`` — полный URL endpoint (напр.
      ``https://api.openai.com/v1/chat/completions``).
    * ``OPENAI_PROMPT`` — необязательный системный контекст (system message).
    * ``OPENAI_MODEL`` — необязательно, по умолчанию ``gpt-4o-mini``.
    """
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    url = (os.environ.get("OPENAI_API_URL") or "").strip()
    env_prompt = (os.environ.get("OPENAI_PROMPT") or "").strip()
    model = (os.environ.get("OPENAI_MODEL") or "gpt-4o-mini").strip()

    if not api_key:
        print("Задайте OPENAI_API_KEY в окружении.", file=sys.stderr)
        return 2
    if not url:
        print(
            "Задайте OPENAI_API_URL — полный URL Chat Completions "
            "(например https://api.openai.com/v1/chat/completions).",
            file=sys.stderr,
        )
        return 2

    user_text = (cli_prompt or "").strip()
    messages: list[dict[str, str]] = []
    if env_prompt:
        messages.append({"role": "system", "content": env_prompt})
    if user_text:
        messages.append({"role": "user", "content": user_text})
    if not messages:
        print(
            "Пустой запрос: укажите текст после --gpt и/или OPENAI_PROMPT в .env.",
            file=sys.stderr,
        )
        return 2

    payload = {"model": model, "messages": messages}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
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
        print(f"HTTP {e.code}: {body or e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"Сеть: {e.reason}", file=sys.stderr)
        return 1
    except OSError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        print(raw, end="" if raw.endswith("\n") else "\n")
        return 1

    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 1

    msg = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = (msg or {}).get("content") if isinstance(msg, dict) else None
    if not isinstance(content, str):
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 1

    print(content, end="" if content.endswith("\n") else "\n")
    return 0
