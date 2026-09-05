# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import argparse
import logging
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List

import rates_unified_cache as ucc
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, UserAlreadyParticipantError
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.utils import get_peer_id

from env_loader import load_repo_dotenv
from userbot.cache_writer import key_for_source, write_source_snapshot
from userbot.chats import event_chat_keys, keys_from_event, lookup_source, normalize_chat_ref
from userbot.config import load_settings
from userbot.models import ParsedRate, SourceConfig
from userbot.parser import compile_rules, parse_message
from userbot.sources_config import USERBOT_SOURCES

logger = logging.getLogger(__name__)
_ROOT = Path(__file__).resolve().parent.parent


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


def _read_existing_source_snapshot(cfg: SourceConfig) -> List[ParsedRate]:
    doc = ucc.load_unified()
    hit = ucc.l1_get_valid(doc, key_for_source(cfg.source_id))
    if hit is None:
        return []
    payload = hit[1]
    if not isinstance(payload, list):
        return []
    out: List[ParsedRate] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            out.append(
                ParsedRate(
                    source_id=str(row.get("source_id") or cfg.source_id),
                    source_name=str(row.get("source_name") or cfg.name),
                    currency=str(row.get("currency") or "").strip().upper(),
                    category=str(row.get("category") or "").strip().lower(),
                    rate=float(row.get("rate") or 0),
                    message_id=int(row.get("message_id") or 0),
                    message_unix=float(row.get("message_unix") or 0),
                    chat=str(row.get("chat") or cfg.chat),
                    city=str(row.get("city") or cfg.city),
                )
            )
        except (TypeError, ValueError):
            continue
    return out


def _merge_with_existing_snapshot(cfg: SourceConfig, rows: Iterable[ParsedRate]) -> List[ParsedRate]:
    combined: List[ParsedRate] = []
    combined.extend(_read_existing_source_snapshot(cfg))
    combined.extend(list(rows))
    return _pick_latest_per_currency(combined)


async def _ensure_channel(client: TelegramClient, cfg: SourceConfig) -> object:
    entity = await client.get_entity(cfg.chat)
    try:
        await client(JoinChannelRequest(entity))
        logger.info("joined %s (%s)", cfg.source_id, cfg.chat)
    except UserAlreadyParticipantError:
        pass
    except FloodWaitError as e:
        logger.warning("join %s flood wait %ss", cfg.source_id, getattr(e, "seconds", "?"))
    except Exception as e:
        logger.warning("join %s failed: %s", cfg.source_id, e)
    return entity


def _register_entity_keys(
    cfg_by_chat: Dict[str, SourceConfig],
    cfg: SourceConfig,
    entity: object,
) -> None:
    username = getattr(entity, "username", None)
    raw_id = getattr(entity, "id", None)
    keys = list(event_chat_keys(username=username, chat_id=raw_id if raw_id is not None else None))
    keys.append(normalize_chat_ref(cfg.chat))
    try:
        keys.extend(event_chat_keys(chat_id=int(get_peer_id(entity))))
    except Exception:
        pass
    seen: set[str] = set()
    uniq: list[str] = []
    for key in keys:
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(key)
        cfg_by_chat[key] = cfg
    logger.info("listen %s keys=%s", cfg.source_id, ",".join(uniq))


async def _build_chat_index(
    client: TelegramClient,
    sources: Iterable[SourceConfig],
) -> Dict[str, SourceConfig]:
    cfg_by_chat: Dict[str, SourceConfig] = {}
    for cfg in sources:
        cfg_by_chat[normalize_chat_ref(cfg.chat)] = cfg
        try:
            entity = await _ensure_channel(client, cfg)
        except Exception as e:
            logger.warning("resolve %s (%s) failed: %s", cfg.source_id, cfg.chat, e)
            continue
        _register_entity_keys(cfg_by_chat, cfg, entity)
    return cfg_by_chat


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
    if not found:
        logger.warning("bootstrap: no matching message for %s", cfg.source_id)
        return
    latest = _merge_with_existing_snapshot(cfg, found)
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

    cfg_by_chat = await _build_chat_index(client, USERBOT_SOURCES)
    compiled = {c.source_id: compile_rules(c) for c in USERBOT_SOURCES}

    for cfg in USERBOT_SOURCES:
        await _bootstrap_source(
            client,
            cfg,
            limit=s.bootstrap_messages_limit,
        )

    async def _process_event_message(event: object, *, event_kind: str) -> None:
        cfg = lookup_source(cfg_by_chat, keys_from_event(event))
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
        latest = _merge_with_existing_snapshot(cfg, rows)
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

