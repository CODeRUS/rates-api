# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import argparse
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

from telethon import TelegramClient, events

from env_loader import load_repo_dotenv
from userbot.cache_writer import write_source_snapshot
from userbot.config import load_settings
from userbot.models import ParsedRate, SourceConfig
from userbot.parser import compile_rules, parse_message
from userbot.sources_config import USERBOT_SOURCES

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent


def _normalize_chat_ref(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    if s.startswith("@"):
        return "@" + s[1:].lower()
    return s


def _group_by_source(rows: Iterable[ParsedRate]) -> Dict[str, List[ParsedRate]]:
    out: Dict[str, List[ParsedRate]] = defaultdict(list)
    for r in rows:
        out[r.source_id].append(r)
    return out


def _pick_latest_per_currency(rows: Iterable[ParsedRate]) -> List[ParsedRate]:
    best: Dict[str, ParsedRate] = {}
    for r in rows:
        prev = best.get(r.currency)
        if prev is None or r.message_unix > prev.message_unix:
            best[r.currency] = r
    return list(best.values())


def _rates_brief(rows: Iterable[ParsedRate]) -> str:
    parts: List[str] = []
    for r in rows:
        parts.append(f"{r.currency}:{r.rate:.4f} [{r.category}]")
    return ", ".join(parts)


async def _bootstrap_source(
    client: TelegramClient,
    cfg: SourceConfig,
    *,
    limit: int,
) -> None:
    rules = compile_rules(cfg)
    found: List[ParsedRate] = []
    async for msg in client.iter_messages(cfg.chat, limit=limit):
        text = getattr(msg, "message", "") or ""
        if not text.strip():
            continue
        parsed = parse_message(
            source_id=cfg.source_id,
            source_name=cfg.name,
            chat=cfg.chat,
            city=cfg.city,
            rules=rules,
            text=text,
            message_id=int(msg.id),
            message_unix=float(msg.date.timestamp()),
        )
        if parsed:
            found.extend(parsed)
            # самое свежее подходящее сообщение найдено (iter_messages идет от новых к старым)
            break
    if not found:
        logger.warning("bootstrap: no matching message for %s", cfg.source_id)
        return
    latest = _pick_latest_per_currency(found)
    write_source_snapshot(source_id=cfg.source_id, rows=latest)
    logger.info(
        "bootstrap: %s matched msg=%s rates=%d (%s)",
        cfg.source_id,
        latest[0].message_id if latest else "-",
        len(latest),
        _rates_brief(latest),
    )


async def _run(*, login_only: bool, login_phone: str) -> None:
    load_repo_dotenv(_ROOT)
    s = load_settings()
    session_path = s.session_dir / "userbot"
    client = TelegramClient(
        str(session_path),
        s.api_id,
        s.api_hash,
        device_model=s.device_model,
        system_version=s.system_version,
        app_version=s.app_version,
        lang_code=s.lang_code,
    )
    if login_only:
        if not login_phone:
            raise RuntimeError("Для --login укажите --phone +7999...")
        await client.start(phone=login_phone)
    else:
        await client.start()
    logger.info("userbot logged in")
    if login_only:
        await client.disconnect()
        return

    for cfg in USERBOT_SOURCES:
        await _bootstrap_source(
            client,
            cfg,
            limit=s.bootstrap_messages_limit,
        )

    cfg_by_chat = {_normalize_chat_ref(c.chat): c for c in USERBOT_SOURCES}
    compiled = {c.source_id: compile_rules(c) for c in USERBOT_SOURCES}

    async def _process_event_message(event: object, *, event_kind: str) -> None:
        chat = getattr(event.chat, "username", None)
        chat_key = ("@" + chat.lower()) if chat else str(event.chat_id)
        cfg = cfg_by_chat.get(chat_key)
        if cfg is None:
            return
        msg = getattr(event, "message", None)
        if msg is None:
            return
        text = getattr(msg, "message", "") or ""
        rows = parse_message(
            source_id=cfg.source_id,
            source_name=cfg.name,
            chat=cfg.chat,
            city=cfg.city,
            rules=compiled[cfg.source_id],
            text=text,
            message_id=int(msg.id),
            message_unix=float(msg.date.timestamp()),
        )
        if not rows:
            return
        latest = _pick_latest_per_currency(rows)
        write_source_snapshot(source_id=cfg.source_id, rows=latest)
        logger.info(
            "%s: %s matched msg=%s rates=%d (%s)",
            event_kind,
            cfg.source_id,
            msg.id,
            len(latest),
            _rates_brief(latest),
        )

    @client.on(events.NewMessage)
    async def _on_msg(event: events.NewMessage.Event) -> None:
        await _process_event_message(event, event_kind="update")

    @client.on(events.MessageEdited)
    async def _on_msg_edited(event: events.MessageEdited.Event) -> None:
        await _process_event_message(event, event_kind="edited")

    await client.run_until_disconnected()


def main() -> None:
    p = argparse.ArgumentParser(add_help=True)
    p.add_argument("--login", action="store_true", help="Только авторизация и запись session")
    p.add_argument("--phone", default="", help="Номер телефона для --login, например +79990001122")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_run(login_only=bool(args.login), login_phone=(args.phone or "").strip()))


if __name__ == "__main__":
    main()

