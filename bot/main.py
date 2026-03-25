#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram-бот (Telethon) для команды /rates.

Запуск из корня репозитория::

    export TELEGRAM_API_ID="611335"
    export TELEGRAM_API_HASH="…"
    export TELEGRAM_BOT_TOKEN="…"
    python -m bot.main

Секреты не коммитьте. Файл сессии: ``bot/rates_bot.session`` (в .gitignore).
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Set

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from telethon import TelegramClient, events

from bot.summary_adapter import get_summary_text, split_for_telegram

logger = logging.getLogger(__name__)


def _env_int(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _credentials_ok() -> bool:
    return (
        _env_int("TELEGRAM_API_ID") is not None
        and bool(_env("TELEGRAM_API_HASH"))
        and bool(_env("TELEGRAM_BOT_TOKEN"))
    )


async def _main_async() -> None:
    api_id = _env_int("TELEGRAM_API_ID")
    api_hash = _env("TELEGRAM_API_HASH")
    bot_token = _env("TELEGRAM_BOT_TOKEN")
    if api_id is None or not api_hash or not bot_token:
        print(
            "Задайте TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN в окружении.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    sess_dir_raw = (os.environ.get("TELETHON_SESSION_DIR") or "").strip()
    sess_dir = Path(sess_dir_raw) if sess_dir_raw else (_ROOT / "bot")
    sess_dir.mkdir(parents=True, exist_ok=True)
    session_path = sess_dir / "rates_bot"
    client = TelegramClient(str(session_path), api_id, api_hash)

    rates_busy_guard = asyncio.Lock()
    rates_busy_chats: Set[int] = set()

    @client.on(events.NewMessage(pattern=r"(?i)^/start(?:@\S+)?$"))
    async def on_start(event: events.NewMessage.Event) -> None:
        await event.respond("Команда /rates — сводка курсов THB")

    @client.on(events.NewMessage(pattern=r"(?i)^/rates(?:@\S+)?"))
    async def on_rates(event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        async with rates_busy_guard:
            if chat_id in rates_busy_chats:
                await event.respond(
                    "Уже выполняется предыдущий /rates. Дождитесь результата."
                )
                return
            rates_busy_chats.add(chat_id)

        msg = event.message.message or ""
        tokens = msg.split()
        refresh = (
            len(tokens) > 1
            and tokens[1].lower() in ("refresh", "r", "--refresh")
        )
        try:
            status_msg = await event.respond("Идёт получение…")
            try:
                text = await asyncio.to_thread(get_summary_text, refresh=refresh)
            except Exception:
                logger.exception("compute_summary_rows failed")
                await status_msg.edit("Не удалось собрать сводку. Попробуйте позже.")
                return
            chunks = split_for_telegram(text)
            if not chunks or (len(chunks) == 1 and not chunks[0].strip()):
                await status_msg.edit("(пустая сводка)")
                return
            await status_msg.edit(chunks[0])
            for chunk in chunks[1:]:
                await event.respond(chunk)
        finally:
            async with rates_busy_guard:
                rates_busy_chats.discard(chat_id)

    logger.info("Telethon polling (bot)…")
    await client.start(bot_token=bot_token)
    await client.run_until_disconnected()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not _credentials_ok():
        print(
            "Нужны TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_BOT_TOKEN.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
