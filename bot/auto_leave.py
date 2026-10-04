# -*- coding: utf-8 -*-
"""Leave a group or supergroup as soon as this bot is added to it."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

from telethon import utils
from telethon.tl import types as tl_types

logger = logging.getLogger(__name__)

_leaving: set[int] = set()

_INACTIVE_PARTICIPANTS = (
    tl_types.ChannelParticipantLeft,
    tl_types.ChannelParticipantBanned,
)


def added_chat_id(update: object, self_id: int) -> int | None:
    """Return the Bot API chat id when this update adds the bot, otherwise None."""
    if isinstance(update, tl_types.UpdateChannelParticipant):
        if int(update.user_id) != self_id or not _became_member(
            update.prev_participant, update.new_participant
        ):
            return None
        return utils.get_peer_id(tl_types.PeerChannel(update.channel_id))
    if isinstance(update, tl_types.UpdateChatParticipant):
        if int(update.user_id) != self_id or not _became_member(
            update.prev_participant, update.new_participant
        ):
            return None
        return utils.get_peer_id(tl_types.PeerChat(update.chat_id))
    if isinstance(update, tl_types.UpdateChatParticipantAdd):
        if int(update.user_id) != self_id:
            return None
        return utils.get_peer_id(tl_types.PeerChat(update.chat_id))
    message = getattr(update, "message", None)
    action = getattr(message, "action", None)
    if not isinstance(message, tl_types.MessageService) or action is None:
        return None
    if isinstance(action, tl_types.MessageActionChatAddUser):
        if self_id not in {int(user_id) for user_id in action.users}:
            return None
        return utils.get_peer_id(message.peer_id)
    if isinstance(
        action,
        (
            tl_types.MessageActionChatJoinedByLink,
            tl_types.MessageActionChatJoinedByRequest,
            tl_types.MessageActionChatJoinedViaCommunity,
        ),
    ):
        if _peer_user_id(message.from_id) != self_id:
            return None
        return utils.get_peer_id(message.peer_id)
    return None


def _became_member(prev: object, new: object) -> bool:
    return _is_member(new) and not _is_member(prev)


def _is_member(participant: object) -> bool:
    if participant is None or isinstance(participant, _INACTIVE_PARTICIPANTS):
        return False
    return True


def _peer_user_id(peer: object) -> int | None:
    user_id = getattr(peer, "user_id", None)
    if user_id is None:
        return None
    return int(user_id)


def leave_chat(chat_id: int) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/leaveChat",
        data=json.dumps({"chat_id": chat_id}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = json.loads(exc.read().decode())
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or "leaveChat failed")


async def handle_membership_update(update: object, self_id: int) -> None:
    import asyncio

    chat_id = added_chat_id(update, self_id)
    if chat_id is None or chat_id in _leaving:
        return
    _leaving.add(chat_id)
    try:
        logger.info("bot added to chat_id=%s, leaving", chat_id)
        await asyncio.to_thread(leave_chat, chat_id)
        logger.info("left chat_id=%s", chat_id)
    except Exception:
        logger.exception("failed to leave chat_id=%s", chat_id)
    finally:
        _leaving.discard(chat_id)
