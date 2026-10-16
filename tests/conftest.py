"""Shared fakes: a G-Earth extension stand-in, a scripted responder, and packet builders.

Packet builders return the *payload values* of a packet in order; :func:`packet` turns
them into an ``HPacket`` (ints, strings, bools and raw bytes are appended by type).

Attributes:
    TO_CLIENT: Shorthand for ``Direction.TO_CLIENT`` (incoming).
    TO_SERVER: Shorthand for ``Direction.TO_SERVER`` (outgoing).
    IN_NAMES (list[str]): Incoming packet names the fake extension knows.
    OUT_NAMES (list[str]): Outgoing packet names the fake extension knows.
    ACTION_NAMES (set[str]): Outgoing avatar action packets, for :meth:`FakeExtension.sent_actions`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import pytest

from habbo_ask_bot.gpython import Direction, HMessage, HPacket
from habbo_ask_bot.llm import Reply, ReplyKind
from habbo_ask_bot.protocol import ChatMode, OutgoingChat, parse_outgoing_chat

TO_CLIENT = Direction.TO_CLIENT
TO_SERVER = Direction.TO_SERVER

IN_NAMES = [
    "Chat",
    "Shout",
    "Whisper",
    "Users",
    "UserRemove",
    "RoomReady",
    "UserObject",
    "FloodControl",
    "CloseConnection",
    "RemainingMutePeriod",
    "UserUpdate",
    "Dance",
    "Expression",
    "Sleep",
    "FloorHeightMap",
    "RoomEntryTile",
]
OUT_NAMES = [
    "Chat",
    "Shout",
    "Whisper",
    "InfoRetrieve",
    "GetHeightMap",
    "MoveAvatar",
    "Dance",
    "ChangePosture",
    "AvatarExpression",
    "Sign",
    "LookTo",
]
ACTION_NAMES = {"MoveAvatar", "Dance", "ChangePosture", "AvatarExpression", "Sign", "LookTo"}


def packet(*values: Any) -> HPacket:
    """An HPacket with a dummy header and the given payload, ready to read.

    Args:
        *values: Payload values, appended in order by type.

    Returns:
        The packet, positioned at the payload.
    """
    return HPacket(1, *values)


def player(uid: int, name: str, index: int, motto: str = "", x: int = 3, y: int = 4) -> list[Any]:
    """Flattened ``Users`` entry for a player (type 1) in the live AIR layout.

    That's G-Rust's documented fields plus a trailing undocumented int (-1).

    Args:
        uid: Account id.
        name: Habbo name.
        index: Room index.
        motto: Motto.
        x: X tile.
        y: Y tile.

    Returns:
        The entry's payload values.
    """
    return [
        uid,
        name,
        motto,
        "hd-180-1",
        index,
        x,
        y,
        "0.0",
        2,
        1,
        "M",
        0,
        0,
        "",
        "",
        120,
        False,
        -1,
    ]


def user_update(*entries: tuple, extra_ints: int = 1) -> HPacket:
    """Build a ``UserUpdate`` packet.

    Args:
        *entries: ``(index, x, y, status)`` or ``(index, x, y, z, head, body, status)``
            tuples.
        extra_ints: 1 for the current AIR layout (an undocumented int after the
            directions); 0 for the layout G-Earth/G-Rust/xabbo document.

    Returns:
        The packet.
    """
    values: list[Any] = [len(entries)]
    for entry in entries:
        index, x, y, z, head, body, status = (
            entry if len(entry) == 7 else (*entry[:3], "0.0", 2, 2, entry[3])
        )
        values += [index, x, y, z, head, body, *([0] * extra_ints), status]
    return packet(*values)


def floor_map(rows: list[str]) -> HPacket:
    """Build a ``FloorHeightMap`` packet.

    Args:
        rows: Heightmap rows (``x`` = not floor); joined with carriage returns like
            the real server does.

    Returns:
        The packet.
    """
    return packet(False, 7, "\r".join(rows))


def pet(uid: int, name: str, index: int) -> list[Any]:
    """Flattened ``Users`` entry for a pet (type 2).

    Args:
        uid: Pet id.
        name: Pet name.
        index: Room index.

    Returns:
        The entry's payload values.
    """
    return [
        uid,
        name,
        "",
        "pet",
        index,
        1,
        1,
        "0.0",
        2,
        2,
        1,
        99,
        "Owner",
        1,
        False,
        False,
        False,
        False,
        False,
        False,
        5,
        "std",
    ]


def bot_entity(uid: int, name: str, index: int) -> list[Any]:
    """Flattened ``Users`` entry for a room bot (type 4) with two skills.

    Args:
        uid: Bot id.
        name: Bot name.
        index: Room index.

    Returns:
        The entry's payload values.
    """
    # Skills: int count 2, then two shorts (encoded here as raw bytes).
    return [
        uid,
        name,
        "beep",
        "fig",
        index,
        1,
        1,
        "0.0",
        2,
        4,
        "F",
        99,
        "Owner",
        2,
        b"\x00\x01\x00\x02",
    ]


def users_packet(*entries: list[Any]) -> HPacket:
    """Build a ``Users`` packet.

    Args:
        *entries: Entries from :func:`player`, :func:`pet` or :func:`bot_entity`.

    Returns:
        The packet, with the entry count first.
    """
    values: list[Any] = [len(entries)]
    for entry in entries:
        values.extend(entry)
    return packet(*values)


def incoming_chat(index: int, text: str, style: int = 0, links: int = 0) -> list[Any]:
    """Payload values of an incoming ``Chat`` / ``Shout`` / ``Whisper``.

    Args:
        index: Speaker's room index.
        text: The message.
        style: Bubble style.
        links: Number of link entries to include.

    Returns:
        The payload values.
    """
    values: list[Any] = [index, text, 0, style, links]
    for i in range(links):
        values += [f"link{i}", f"https://example.com/{i}", True]
    values.append(-1)
    return values


@dataclass
class FakeExtension:
    """Stand-in for g-python's ``Extension``: records intercepts, events and sends.

    Attributes:
        intercepts: Callbacks keyed by ``(direction, packet name)``.
        events: Callbacks keyed by event name.
        sent: Every packet sent to the server.
        console: ``(color, text)`` lines written to the G-Earth console.
        packet_infos: Known packet names per direction, like G-Earth provides.
        connection_info: The game connection details G-Earth provides.
    """

    intercepts: dict = field(default_factory=lambda: defaultdict(list))
    events: dict = field(default_factory=lambda: defaultdict(list))
    sent: list = field(default_factory=list)
    console: list = field(default_factory=list)
    packet_infos: dict = field(
        default_factory=lambda: {
            TO_CLIENT: {n: [{"Name": n}] for n in IN_NAMES},
            TO_SERVER: {n: [{"Name": n}] for n in OUT_NAMES},
        }
    )
    connection_info: dict = field(
        default_factory=lambda: {
            "host": "game-us.habbo.com",
            "client_type": "FLASH",
            "hotel_version": "MAC63-test",
        }
    )

    def intercept(self, direction, callback, id=-1, mode="default"):  # noqa: A002
        """Register an intercept.

        Args:
            direction: ``TO_CLIENT`` or ``TO_SERVER``.
            callback: Called with an ``HMessage``.
            id: Packet name.
            mode: Ignored.
        """
        self.intercepts[(direction, id)].append(callback)

    def on_event(self, name, fn):
        """Register an event callback.

        Args:
            name: Event name, e.g. ``"connection_start"``.
            fn: Called with no arguments.
        """
        self.events[name].append(fn)

    def send_to_server(self, pkt):
        """Record a sent packet.

        Args:
            pkt: The packet.
        """
        self.sent.append(pkt)

    def write_to_console(self, text, color="black", mention_title=True):
        """Record a console line.

        Args:
            text: The line.
            color: Console colour.
            mention_title: Ignored.
        """
        self.console.append((color, text))

    def fire(self, direction, name, *values: Any, pkt: HPacket | None = None) -> HMessage:
        """Deliver a packet to every intercept for ``name``.

        Args:
            direction: ``TO_CLIENT`` or ``TO_SERVER``.
            name: Packet name.
            *values: Payload values (ignored when ``pkt`` is given).
            pkt: A ready-made packet.

        Returns:
            The message, so tests can check ``is_blocked``.
        """
        pkt = pkt if pkt is not None else packet(*values)
        msg = HMessage(pkt, direction, 0)
        for callback in self.intercepts[(direction, name)]:
            callback(msg)
            pkt.reset()
        return msg

    def sent_actions(self) -> list[tuple[str, tuple[int, ...]]]:
        """Avatar action packets the bot sent.

        Returns:
            ``(name, int args)`` per action packet, in send order.
        """
        out = []
        for pkt in self.sent:
            if pkt.incomplete_identifier in ACTION_NAMES:
                count = (len(pkt.bytearray) - 6) // 4
                pkt.reset()
                out.append((pkt.incomplete_identifier, tuple(pkt.read_int() for _ in range(count))))
                pkt.reset()
        return out

    def event(self, name: str) -> None:
        """Fire an event.

        Args:
            name: Event name.
        """
        for fn in self.events[name]:
            fn()

    def sent_chat(self) -> list[OutgoingChat]:
        """Chat, shout and whisper packets the bot sent.

        Returns:
            The decoded packets, in send order.
        """
        out = []
        for pkt in self.sent:
            name = pkt.incomplete_identifier
            if name in {m.packet_name for m in ChatMode}:
                pkt.reset()
                out.append(parse_outgoing_chat(ChatMode(name), pkt))
                pkt.reset()
        return out

    def console_text(self) -> str:
        """Everything written to the console.

        Returns:
            The lines joined with newlines.
        """
        return "\n".join(text for _, text in self.console)


@dataclass
class FakeResponder:
    """Scripted stand-in for :class:`~habbo_ask_bot.llm.ClaudeResponder`.

    Attributes:
        replies: Replies to return, in order; once empty, a default OK reply is used.
        calls: The arguments of every call.
        default_text: Text of the default reply.
    """

    replies: list[Reply] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)
    default_text: str = "Kuala Lumpur is the capital of Malaysia."

    def respond(self, *, system, messages, web_search, claude):
        """Record the call and return the next scripted reply.

        Args:
            system: System prompt.
            messages: Conversation.
            web_search: Whether web search was allowed.
            claude: Ignored.

        Returns:
            The next reply from :attr:`replies`, or a default OK reply.
        """
        self.calls.append({"system": system, "messages": list(messages), "web_search": web_search})
        if self.replies:
            return self.replies.pop(0)
        return Reply(
            ReplyKind.OK,
            text=self.default_text,
            cost_usd=0.001,
            input_tokens=100,
            output_tokens=20,
            stop_reason="end_turn",
        )


class InlineExecutor:
    """Runs submitted work immediately, so tests are deterministic."""

    def submit(self, fn, *args, **kwargs):
        """Run the work now.

        Args:
            fn: The function.
            *args: Positional arguments.
            **kwargs: Keyword arguments.
        """
        fn(*args, **kwargs)

    def shutdown(self, wait=True, cancel_futures=False):
        """Do nothing (nothing is ever pending).

        Args:
            wait: Ignored.
            cancel_futures: Ignored.
        """
        pass


@pytest.fixture
def fake_ext() -> FakeExtension:
    """Fixture: a fresh :class:`FakeExtension`.

    Returns:
        The fake extension.
    """
    return FakeExtension()
