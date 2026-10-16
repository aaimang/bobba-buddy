"""Packet parsing and building, including live captures from the AIR client."""

import pytest
from conftest import bot_entity, incoming_chat, packet, pet, player, users_packet

from habbo_ask_bot.gpython import HPacket
from habbo_ask_bot.protocol import (
    ChatMode,
    ProtocolError,
    UserKind,
    build_chat,
    build_shout,
    build_whisper,
    parse_incoming_chat,
    parse_outgoing_chat,
    parse_seconds,
    parse_user_object,
    parse_user_remove,
    parse_users,
)

# The complete first entry of the live Users packet from the bad-packet log (a room bot).
LIVE_BOT_ENTRY = bytes.fromhex(
    "00 01 1a 06 00 07 43 79 6e 64 69 69 2e 00 22 43 61 6e 20 74 61 6c 6b 2c 20 77 61 6c 6b 2c "
    "20 64 61 6e 63 65 20 61 6e 64 20 64 72 65 73 73 20 75 70 00 69 68 72 2d 33 30 34 30 2d 31 "
    "33 34 32 2e 68 64 2d 36 32 39 2d 32 2e 63 68 2d 33 30 30 35 2d 39 32 2d 31 34 30 38 2e 6c "
    "67 2d 33 31 37 34 2d 39 37 2d 39 37 2e 68 65 2d 33 30 38 32 2d 39 32 2e 66 61 2d 33 32 37 "
    "36 2d 31 34 31 34 2e 63 61 2d 33 32 31 39 2d 31 31 30 2e 63 63 2d 33 30 30 38 2d 31 33 32 "
    "36 2d 31 33 32 36 00 00 00 00 00 00 00 1e 00 00 00 18 00 04 35 2e 33 32 00 00 00 02 00 00 "
    "00 04 00 01 66 03 d9 73 6f 00 0a 55 4e 5f 46 6f 75 6e 64 65 72 00 00 00 0a 00 01 00 02 00 "
    "03 00 04 00 05 00 13 00 14 00 15 00 16 00 17"
)


# A live single-player Users packet (logs/bad-packets/Users-20261013-233453.hex).
LIVE_PLAYER_PACKET = """\
00 00 00 d0 05 0b 00 00 00 01 03 fe 09 bd 00 05 2e 48 61 6e 2e 00 0a 7c 55 4e 7c 20 47 4f 56 20
7c 00 69 68 72 2d 35 35 30 33 2d 33 32 2e 68 64 2d 36 32 31 39 2d 37 2d 31 33 36 34 2e 63 68 2d
35 33 35 31 2d 39 34 2d 31 33 32 33 2e 6c 67 2d 34 30 31 31 2e 73 68 2d 35 34 36 34 2d 39 34 2d
31 33 32 38 2e 68 61 2d 36 32 39 31 2e 68 65 2d 33 37 34 30 2e 63 61 2d 35 37 37 31 2d 39 34 2d
31 33 32 33 2e 63 63 2d 34 30 31 30 00 00 03 a3 00 00 00 24 00 00 00 25 00 04 30 2e 31 35 00 00
00 02 00 00 00 01 00 01 66 00 08 11 4a 00 00 00 03 00 16 3c 55 4e 3e 20 47 6f 76 65 72 6e 6d 65
6e 74 20 5b 34 30 30 6b 5d 00 00 00 00 20 29 00 ff ff ff ff"""


def reread(pkt: HPacket) -> HPacket:
    """Rewind a packet so it can be read from the start of the payload.

    Args:
        pkt: The packet.

    Returns:
        The same packet.
    """
    pkt.reset()
    return pkt


class TestChat:
    @pytest.mark.parametrize("mode", list(ChatMode))
    def test_incoming(self, mode):
        event = parse_incoming_chat(mode, packet(*incoming_chat(7, "/q hai apa khabar?", 3)))
        assert (event.user_index, event.text, event.style) == (7, "/q hai apa khabar?", 3)

    def test_incoming_with_links(self):
        event = parse_incoming_chat(ChatMode.CHAT, packet(*incoming_chat(2, "see link", links=2)))
        assert event.text == "see link"

    def test_incoming_utf8(self):
        text = "/q café ñandú 😀"
        assert parse_incoming_chat(ChatMode.CHAT, packet(*incoming_chat(1, text))).text == text

    def test_build_chat_round_trip(self):
        pkt = build_chat("hello 😀", 5, 1234)
        assert pkt.incomplete_identifier == "Chat"
        event = parse_outgoing_chat(ChatMode.CHAT, reread(pkt))
        assert (event.text, event.style, event.tail) == ("hello 😀", 5, 1234)

    def test_build_shout_round_trip(self):
        event = parse_outgoing_chat(ChatMode.SHOUT, reread(build_shout("HEY", 0)))
        assert (event.text, event.style, event.tail) == ("HEY", 0, None)

    def test_build_whisper_round_trip(self):
        event = parse_outgoing_chat(ChatMode.WHISPER, reread(build_whisper("Bob", "psst hi", 2)))
        assert (event.whisper_target, event.text, event.style) == ("Bob", "psst hi", 2)

    def test_outgoing_chat_without_tail(self):
        event = parse_outgoing_chat(ChatMode.CHAT, packet("hi", 0))
        assert event.tail is None

    def test_truncated_packet_raises(self):
        with pytest.raises(ProtocolError):
            parse_incoming_chat(ChatMode.CHAT, packet(1))
        pkt = packet(1, "hello", 0, 0)
        pkt.bytearray = pkt.bytearray[:-6]  # cut into the string
        pkt.fix_length()
        with pytest.raises(ProtocolError):
            parse_incoming_chat(ChatMode.CHAT, reread(pkt))


class TestRoom:
    def test_users_with_player_pet_and_bot(self):
        users, problem = parse_users(
            users_packet(
                player(10, "Owner", 0, "my motto"),
                pet(11, "Rex", 1),
                bot_entity(12, "Frank", 2),
                player(13, "Ali", 3),
            )
        )
        assert problem is None
        assert [(u.name, u.index, u.kind) for u in users] == [
            ("Owner", 0, UserKind.PLAYER),
            ("Rex", 1, UserKind.PET),
            ("Frank", 2, UserKind.BOT),
            ("Ali", 3, UserKind.PLAYER),
        ]
        assert users[0].motto == "my motto"

    def test_unknown_user_type_is_reported(self):
        entry = player(1, "X", 0)[:9] + [9]
        users, problem = parse_users(users_packet(entry))
        assert users == [] and "implausible entry header" in problem

    def test_live_bot_entry(self):
        # First entry of a live Users packet (AIR MAC63-202610131318): a room bot.
        users, problem = parse_users(packet(1, LIVE_BOT_ENTRY))
        assert problem is None
        [bot] = users
        assert (bot.name, bot.index, bot.kind, bot.x, bot.y) == ("Cyndii.", 0, UserKind.BOT, 30, 24)
        assert bot.motto == "Can talk, walk, dance and dress up"

    def test_live_player_entry_with_extra_int(self):
        lines = LIVE_PLAYER_PACKET.splitlines()
        users, problem = parse_users(reread(HPacket.from_bytes(bytes.fromhex(" ".join(lines)))))
        assert problem is None
        [han] = users
        assert (han.name, han.index, han.kind, han.x, han.y) == (
            ".Han.",
            931,
            UserKind.PLAYER,
            36,
            37,
        )
        assert han.motto == "|UN| GOV |"

    def test_documented_player_layout_still_parses(self):
        legacy = player(1, "Old", 0)[:-1]  # without the trailing int
        users, problem = parse_users(users_packet(legacy, player(2, "New", 1)))
        assert problem is None and [u.name for u in users] == ["Old", "New"]

    def test_entry_with_unknown_tail_fields_is_skipped_over(self):
        odd = player(10, "Odd", 0) + [77, "new field"]  # two undocumented fields
        users, problem = parse_users(users_packet(odd, player(11, "Ali", 1), pet(12, "Rex", 2)))
        assert [u.name for u in users] == ["Odd", "Ali", "Rex"]
        assert "entry 0 of 3 (player 'Odd', index 0)" in problem
        assert "tail doesn't match" in problem

    def test_every_entry_with_extra_fields(self):
        entries = [player(i, f"User{i}", i, x=i, y=i) + [i] for i in range(5)]
        users, problem = parse_users(users_packet(*entries))
        assert [u.name for u in users] == [f"User{i}" for i in range(5)]
        assert [(u.x, u.y) for u in users] == [(i, i) for i in range(5)]
        assert problem is not None

    def test_last_entry_with_extra_fields(self):
        users, problem = parse_users(users_packet(player(1, "A", 0), player(2, "B", 1) + [5]))
        assert [u.name for u in users] == ["A", "B"]
        assert "entry 1 of 2" in problem

    def test_garbage_after_good_entries(self):
        pkt = users_packet(player(1, "A", 0), player(2, "B", 1))
        pkt.bytearray = pkt.bytearray[:-30]
        pkt.fix_length()
        users, problem = parse_users(pkt)
        assert [u.name for u in users] == ["A"]
        assert problem

    def test_user_remove_string_index(self):
        assert parse_user_remove(packet("12")) == 12
        with pytest.raises(ProtocolError):
            parse_user_remove(packet("abc"))

    def test_user_object_and_seconds(self):
        assert parse_user_object(packet(99, "Owner", "fig", "M")) == (99, "Owner")
        assert parse_seconds(packet(30)) == 30
