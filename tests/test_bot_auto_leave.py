# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from telethon.tl import types

from bot.auto_leave import added_chat_id

SELF = 100
OTHER = 200


def _service(peer: types.TypePeer, action: types.TypeMessageAction, from_id=None):
    return types.UpdateNewChannelMessage(
        message=types.MessageService(
            id=1,
            peer_id=peer,
            date=None,
            action=action,
            from_id=from_id,
        ),
        pts=1,
        pts_count=1,
    )


class TestAddedChatId(unittest.TestCase):
    def test_supergroup_join(self) -> None:
        update = types.UpdateChannelParticipant(
            channel_id=3320830016,
            date=None,
            actor_id=OTHER,
            user_id=SELF,
            qts=1,
            prev_participant=None,
            new_participant=types.ChannelParticipant(
                user_id=SELF,
                date=None,
            ),
        )
        self.assertEqual(added_chat_id(update, SELF), -1003320830016)

    def test_supergroup_promotion_is_not_a_join(self) -> None:
        update = types.UpdateChannelParticipant(
            channel_id=3320830016,
            date=None,
            actor_id=OTHER,
            user_id=SELF,
            qts=1,
            prev_participant=types.ChannelParticipant(user_id=SELF, date=None),
            new_participant=types.ChannelParticipantAdmin(
                user_id=SELF,
                promoted_by=OTHER,
                date=None,
                admin_rights=types.ChatAdminRights(),
            ),
        )
        self.assertIsNone(added_chat_id(update, SELF))

    def test_other_user_ignored(self) -> None:
        update = types.UpdateChannelParticipant(
            channel_id=3320830016,
            date=None,
            actor_id=OTHER,
            user_id=OTHER,
            qts=1,
            prev_participant=None,
            new_participant=types.ChannelParticipant(user_id=OTHER, date=None),
        )
        self.assertIsNone(added_chat_id(update, SELF))

    def test_basic_group_add(self) -> None:
        update = types.UpdateChatParticipantAdd(
            chat_id=5430064039,
            user_id=SELF,
            inviter_id=OTHER,
            date=1,
            version=1,
        )
        self.assertEqual(added_chat_id(update, SELF), -5430064039)

    def test_service_add_user(self) -> None:
        update = _service(
            types.PeerChannel(55),
            types.MessageActionChatAddUser(users=[SELF, OTHER]),
            from_id=types.PeerUser(OTHER),
        )
        self.assertEqual(added_chat_id(update, SELF), -1000000000055)

    def test_service_joined_by_link(self) -> None:
        update = _service(
            types.PeerChat(77),
            types.MessageActionChatJoinedByLink(inviter_id=OTHER),
            from_id=types.PeerUser(SELF),
        )
        self.assertEqual(added_chat_id(update, SELF), -77)


if __name__ == "__main__":
    unittest.main()
