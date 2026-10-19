"""Habbo AIR (G-Earth "FLASH") packet parsing and building.

Layouts follow G-Realm/G-Rust ``src/extension/parsers/{incoming,outgoing,subparsers}.rs``
and xabbo/core, with fixes for the live client where it has drifted from them.
Packets are always addressed by name: header ids differ between the Windows and macOS
AIR builds, and G-Earth resolves names per connection.

Habbo strings are UTF-8, but g_python's ``read_string()`` defaults to ISO-8859-1, so
every read here decodes UTF-8 explicitly.

Attributes:
    UTF8 (str): The string encoding Habbo uses.
    POSTURE_STAND (int): ``ChangePosture`` value for standing.
    POSTURE_SIT (int): ``ChangePosture`` value for sitting on the floor.
    EXPRESSION_WAVE (int): ``AvatarExpression`` value for a wave.
    EXPRESSION_IDLE (int): ``AvatarExpression`` value the client sends to report
        "I'm idle" (the server then shows Zzz); blocked by anti-idle.
    COPYABLE_EXPRESSIONS (frozenset[int]): Every expression the bot may copy or send:
        wave, blow kiss, laugh, jump, thumbs up.
    FREE_EXPRESSIONS (frozenset[int]): Expressions available without Habbo Club:
        wave and thumbs up.
    MAX_SIGN (int): Highest ``Sign`` value (signs run 0-17).
    LOOK_DIRECTIONS (tuple[tuple[int, int], ...]): ``LookTo`` targets far outside any
        room, so the avatar faces the same way wherever it stands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum

from .gpython import HPacket

UTF8 = "utf-8"


class ProtocolError(ValueError):
    """A packet didn't match the expected layout."""


class ChatMode(Enum):
    """How a chat line was sent; the value is the packet name.

    Attributes:
        CHAT: Normal chat (``Chat``).
        SHOUT: Shout (``Shout``).
        WHISPER: Whisper (``Whisper``).
    """

    CHAT = "Chat"
    SHOUT = "Shout"
    WHISPER = "Whisper"

    @property
    def packet_name(self) -> str:
        """The packet name for this mode (same in both directions)."""
        return self.value


class UserKind(IntEnum):
    """Entity type in ``Users`` entries.

    Attributes:
        PLAYER: A Habbo user.
        PET: A pet.
        OLD_BOT: An old-style (public room) bot.
        BOT: A user-owned bot.
    """

    PLAYER = 1
    PET = 2
    OLD_BOT = 3
    BOT = 4


@dataclass(frozen=True)
class IncomingChat:
    """A chat line received from the server.

    Attributes:
        mode: Chat, shout or whisper.
        user_index: Room index of the speaker.
        text: The message.
        gesture: Gesture shown with the message.
        style: Bubble style.
    """

    mode: ChatMode
    user_index: int
    text: str
    gesture: int
    style: int


@dataclass(frozen=True)
class OutgoingChat:
    """A chat line the owner's client sent.

    Attributes:
        mode: Chat, shout or whisper.
        text: The message only; a whisper's target is split off.
        style: Bubble style.
        whisper_target: Who was whispered to, or ``None``.
        tail: ``Chat``'s trailing int, copied into the bot's own ``Chat`` packets.
    """

    mode: ChatMode
    text: str  # message only; the whisper target is split off
    style: int
    whisper_target: str | None = None
    tail: int | None = None  # Chat's trailing int, copied into the bot's own Chat packets


@dataclass(frozen=True)
class RoomUser:
    """An entity in the room, from a ``Users`` entry header.

    Attributes:
        id: Account (or pet/bot) id.
        name: Display name.
        motto: Motto (players) or description.
        index: Room index, used by every other room packet.
        kind: Player, pet or bot.
        x: Starting x tile.
        y: Starting y tile.
        group: Players: the name of the group they show (their favourite), or ``""``.
        achievement_score: Players: their achievement score (0 if unknown).
    """

    id: int
    name: str
    motto: str
    index: int
    kind: UserKind
    x: int = 0
    y: int = 0
    group: str = ""
    achievement_score: int = 0


def _remaining(packet: HPacket) -> int:
    """Bytes left to read.

    Args:
        packet: The packet being read.

    Returns:
        Bytes after the current read position.
    """
    return len(packet.bytearray) - packet.read_index


def _read_str(packet: HPacket) -> str:
    """Read a u16-length-prefixed UTF-8 string, rejecting truncation (read_string doesn't).

    Args:
        packet: The packet being read.

    Returns:
        The decoded string (invalid UTF-8 is replaced, not fatal).

    Raises:
        ProtocolError: If the length or the string runs past the end of the packet.
    """
    start = packet.read_index
    if _remaining(packet) < 2:
        raise ProtocolError("truncated string")
    end = start + 2 + int.from_bytes(packet.bytearray[start : start + 2], "big")
    if end > len(packet.bytearray):
        raise ProtocolError("truncated string")
    packet.read_index = end
    return bytes(packet.bytearray[start + 2 : end]).decode(UTF8, errors="replace")


def _read_int(packet: HPacket) -> int:
    """Read a signed 32-bit int.

    Args:
        packet: The packet being read.

    Returns:
        The int.

    Raises:
        ProtocolError: If fewer than 4 bytes are left.
    """
    if _remaining(packet) < 4:
        raise ProtocolError("truncated int")
    return packet.read_int()


def parse_incoming_chat(mode: ChatMode, packet: HPacket) -> IncomingChat:
    """Parse an incoming ``Chat`` / ``Shout`` / ``Whisper``.

    Layout: ``i index, s text, i gesture, i style, links…, i tracking``. Only the
    leading fields are read, so changes to the tail don't break parsing.

    Args:
        mode: Which packet this is.
        packet: The packet, positioned at the payload.

    Returns:
        The chat line.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    index = _read_int(packet)
    text = _read_str(packet)
    gesture = _read_int(packet)
    style = _read_int(packet)
    return IncomingChat(mode=mode, user_index=index, text=text, gesture=gesture, style=style)


def parse_outgoing_chat(mode: ChatMode, packet: HPacket) -> OutgoingChat:
    """Parse the owner's outgoing chat.

    Layouts: ``Chat``: ``s text, i style, i tail``. ``Shout``: ``s text, i style``.
    ``Whisper``: ``s "target text", i style``.

    Args:
        mode: Which packet this is.
        packet: The packet, positioned at the payload.

    Returns:
        The chat line, with a whisper's target split off.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    raw = _read_str(packet)
    style = _read_int(packet)
    tail = packet.read_int() if mode is ChatMode.CHAT and _remaining(packet) >= 4 else None
    if mode is ChatMode.WHISPER:
        target, _, text = raw.partition(" ")
        return OutgoingChat(mode, text, style, whisper_target=target or None)
    return OutgoingChat(mode, raw, style, tail=tail)


def build_chat(text: str, style: int, tail: int = -1) -> HPacket:
    """Build an outgoing ``Chat``.

    Args:
        text: The message.
        style: Bubble style.
        tail: Trailing int (copy the owner's own; -1 by default).

    Returns:
        The packet, addressed by name.
    """
    return HPacket(ChatMode.CHAT.packet_name, text, style, tail)


def build_shout(text: str, style: int) -> HPacket:
    """Build an outgoing ``Shout``.

    Args:
        text: The message.
        style: Bubble style.

    Returns:
        The packet, addressed by name.
    """
    return HPacket(ChatMode.SHOUT.packet_name, text, style)


def build_whisper(target: str, text: str, style: int) -> HPacket:
    """Build an outgoing ``Whisper``.

    Args:
        target: Who to whisper to.
        text: The message (shares the 100-character limit with the target name).
        style: Bubble style.

    Returns:
        The packet, addressed by name.
    """
    return HPacket(ChatMode.WHISPER.packet_name, f"{target} {text}", style)


def _read_entity_header(packet: HPacket) -> RoomUser | None:
    """Read the fields every ``Users`` entry starts with.

    Layout: ``i id, s name, s motto, s figure, i room_index, i x, i y, s z, i dir,
    i type``.

    Args:
        packet: The packet, positioned at an entry.

    Returns:
        The user, or ``None`` if the fields decode to nonsense (a sign the read is out
        of step with the packet).

    Raises:
        ProtocolError: If the packet is truncated.
    """
    uid = _read_int(packet)
    name = _read_str(packet)
    motto = _read_str(packet)
    figure = _read_str(packet)
    index = _read_int(packet)
    x = _read_int(packet)
    y = _read_int(packet)
    z = _read_str(packet)
    direction = _read_int(packet)
    kind = _read_int(packet)
    if not 1 <= len(name) <= 64 or any(ord(c) < 32 for c in name) or not figure:
        return None
    if not 0 <= index <= 1_000_000 or not (0 <= x <= 1024 and 0 <= y <= 1024):
        return None
    if not 0 <= direction <= 7 or kind not in UserKind._value2member_map_:
        return None
    try:
        float(z)
    except ValueError:
        return None
    return RoomUser(uid, name, motto, index, UserKind(kind), x, y)


def _skip_entity_tail(
    packet: HPacket, kind: UserKind, player_extra_ints: int = 1
) -> tuple[str, int]:
    """Read past the type-specific fields after an entry's header.

    Players: G-Rust's documented fields, then (live AIR client, MAC63-202610131318) one
    undocumented int, seen as -1.

    Args:
        packet: The packet, positioned just after the header.
        kind: The entry's type.
        player_extra_ints: Undocumented ints after a player's fields; 0 reads the
            documented layout.

    Returns:
        A player's group name and achievement score; ``("", 0)`` for anything else.

    Raises:
        ProtocolError: If the packet is truncated or a bot's skill count is absurd.
    """
    if kind is UserKind.PLAYER:
        # sex, group id, group status, group name, swim figure, achievement score, moderator
        _read_str(packet)
        _read_int(packet)
        _read_int(packet)
        group = _read_str(packet)
        _read_str(packet)
        score = _read_int(packet)
        _skip(packet, 1)
        _skip(packet, 4 * player_extra_ints)
        return group, score
    if kind is UserKind.PET:
        # sub type, owner id, owner name, rarity, 6 bools, level, posture
        _read_int(packet)
        _read_int(packet)
        _read_str(packet)
        _read_int(packet)
        _skip(packet, 6)
        _read_int(packet)
        _read_str(packet)
    elif kind is UserKind.BOT:
        # sex, owner id, owner name, skills (int count + shorts)
        _read_str(packet)
        _read_int(packet)
        _read_str(packet)
        count = _read_int(packet)
        if not 0 <= count <= 1000:
            raise ProtocolError(f"implausible bot skill count {count}")
        _skip(packet, 2 * count)
    # OLD_BOT has no tail.
    return "", 0


def _header_at(packet: HPacket, offset: int) -> bool:
    """Whether a plausible ``Users`` entry starts at ``offset``.

    Args:
        packet: The packet.
        offset: Byte offset to test; the read position is left wherever reading stopped.

    Returns:
        ``True`` if a complete, sensible entry header reads from there.
    """
    packet.read_index = offset
    try:
        return _read_entity_header(packet) is not None
    except ProtocolError:
        return False


def _find_next_header(packet: HPacket, start: int) -> int | None:
    """Scan forward for the next plausible entry.

    A cheap name-length check runs before the full header check.

    Args:
        packet: The packet.
        start: First byte offset to try.

    Returns:
        The offset of the next entry, or ``None`` if none is found.
    """
    data = packet.bytearray
    for offset in range(start, len(data) - 6):
        name_len = int.from_bytes(data[offset + 4 : offset + 6], "big")
        if 1 <= name_len <= 64 and _header_at(packet, offset):
            return offset
    return None


def parse_users(packet: HPacket) -> tuple[list[RoomUser], str | None]:
    """Parse ``Users``: an int count, then per entry a common header and a type-specific tail.

    Only the header is needed. If an entry's tail doesn't match the documented layout
    (the live AIR client has drifted from it before), this finds where the next entry
    starts and carries on, so one odd entry doesn't lose everyone after it.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The users found, plus a description of the first problem (``None`` if the
        packet parsed cleanly). Never raises.
    """
    end = len(packet.bytearray)
    try:
        count = _read_int(packet)
    except ProtocolError as exc:
        return [], str(exc)
    if not 0 <= count <= 1000:
        return [], f"implausible count {count}"

    users: list[RoomUser] = []
    problem: str | None = None
    pos = packet.read_index
    for n in range(count):
        packet.read_index = pos
        try:
            user = _read_entity_header(packet)
        except ProtocolError as exc:
            return users, problem or f"entry {n} of {count}: {exc}"
        if user is None:
            return users, problem or f"entry {n} of {count}: implausible entry header"
        users.append(user)

        tail_start = packet.read_index
        last = n == count - 1
        tail_ok = False
        for extra in (1, 0) if user.kind is UserKind.PLAYER else (0,):
            packet.read_index = tail_start
            try:
                group, score = _skip_entity_tail(packet, user.kind, extra)
                next_pos = packet.read_index
                tail_ok = next_pos == end if last else _header_at(packet, next_pos)
            except ProtocolError:
                tail_ok = False
            if tail_ok:
                break
        if tail_ok:
            if group or score:  # only trusted when the whole tail lined up
                users[-1] = replace(user, group=group, achievement_score=max(score, 0))
            pos = next_pos
            continue

        found = None if last else _find_next_header(packet, tail_start)
        tail_len = (end if last else found if found is not None else end) - tail_start
        problem = problem or (
            f"entry {n} of {count} ({user.kind.name.lower()} {user.name!r}, index "
            f"{user.index}): {tail_len}-byte tail doesn't match the documented layout"
        )
        if last:
            break
        if found is None:
            return users, f"{problem}; couldn't find the next entry"
        pos = found
    return users, problem


def _skip(packet: HPacket, n: int) -> None:
    """Skip ``n`` bytes.

    Args:
        packet: The packet being read.
        n: Bytes to skip.

    Raises:
        ProtocolError: If ``n`` is negative or runs past the end.
    """
    if n < 0 or _remaining(packet) < n:
        raise ProtocolError("truncated packet")
    packet.read_index += n


def parse_user_remove(packet: HPacket) -> int:
    """Parse ``UserRemove``: ``s index`` (the room index as a string on Flash).

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The room index of the user who left.

    Raises:
        ProtocolError: If the packet is truncated or the index isn't a number.
    """
    raw = _read_str(packet)
    try:
        return int(raw)
    except ValueError as exc:
        raise ProtocolError(f"bad UserRemove index {raw!r}") from exc


def parse_user_object(packet: HPacket) -> tuple[int, str]:
    """Parse ``UserObject``: ``i id, s name, …`` (the rest isn't needed).

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The owner's account id and name.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    return _read_int(packet), _read_str(packet)


def parse_seconds(packet: HPacket) -> int:
    """Parse ``FloodControl`` / ``RemainingMutePeriod``: ``i seconds``.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The mute length in seconds.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    return _read_int(packet)


# --- avatar actions -------------------------------------------------------------------
#
# Incoming Dance / Expression / Sleep carry the room index (G-Rust names it user_id).
# UserUpdate status strings look like "/flatctrl 4/sit 0.5 1/sign 3/mv 4,5,0.0/".

POSTURE_STAND = 0
POSTURE_SIT = 1
EXPRESSION_WAVE = 1
EXPRESSION_IDLE = 5  # the client reports "I'm idle" with this; the server then shows Zzz
COPYABLE_EXPRESSIONS = frozenset({1, 2, 3, 6, 7})  # wave, blow kiss, laugh, jump, thumbs up
FREE_EXPRESSIONS = frozenset({1, 7})  # wave, thumbs up; blow kiss, laugh and jump need HC
MAX_SIGN = 17
# LookTo targets far outside any room, so the avatar faces the same way wherever it stands.
LOOK_DIRECTIONS = (
    (-9999, -9999),
    (-500, 500),
    (500, 0),
    (9999, 9999),
    (5000, 9999),
    (0, 9999),
    (-9999, 9999),
    (-9999, 0),
)


@dataclass(frozen=True)
class UserStatus:
    """One entry of a ``UserUpdate``.

    Attributes:
        index: Room index.
        x: Current x tile.
        y: Current y tile.
        actions: Status actions, e.g. ``{"sit": "0.5 1", "sign": "3"}``.
    """

    index: int
    x: int
    y: int
    actions: dict[str, str] = field(default_factory=dict)

    @property
    def sitting(self) -> bool:
        """Whether the status includes ``sit``."""
        return "sit" in self.actions

    @property
    def moving(self) -> bool:
        """Whether the status includes ``mv`` (walking)."""
        return "mv" in self.actions

    @property
    def sign(self) -> int | None:
        """The sign being held (0-17), or ``None``."""
        raw = self.actions.get("sign")
        try:
            return int(raw) if raw is not None else None
        except ValueError:
            return None


def parse_status_actions(raw: str) -> dict[str, str]:
    """Split a ``UserUpdate`` status string into actions.

    ``'/sit 0.5 1/sign 3/'`` becomes ``{'sit': '0.5 1', 'sign': '3'}``.

    Args:
        raw: The status string.

    Returns:
        Action keys mapped to their (possibly empty) values.
    """
    actions: dict[str, str] = {}
    for part in raw.split("/"):
        part = part.strip()
        if part:
            key, _, value = part.partition(" ")
            actions[key] = value
    return actions


def _implausible_status(
    index: int, x: int, y: int, z: str, head: int, body: int, status: str
) -> str:
    """Why a decoded UserUpdate entry can't be real.

    Args:
        index: Room index.
        x: X tile.
        y: Y tile.
        z: Height string.
        head: Head direction.
        body: Body direction.
        status: Status string.

    Returns:
        A short reason, or ``""`` if the entry looks fine.
    """
    if not 0 <= index <= 1_000_000:
        return f"index {index}"
    if not (0 <= x <= 1024 and 0 <= y <= 1024):
        return f"position {x},{y}"
    if not (0 <= head <= 7 and 0 <= body <= 7):
        return f"direction {head}/{body}"
    try:
        float(z)
    except ValueError:
        return f"height {z!r}"
    if status and not status.startswith("/"):
        return f"status {status[:20]!r}"
    return ""


# Ints between the body direction and the status string, newest layout first. The AIR
# client (seen on MAC63-202610131318) sends one extra int there that G-Earth, G-Rust and
# xabbo don't document; the documented layout has none.
_USER_UPDATE_EXTRA_INTS = (1, 0)


def _parse_user_update_entries(
    packet: HPacket, extra_ints: int
) -> tuple[list[UserStatus], str | None]:
    """Parse a whole ``UserUpdate`` assuming one layout.

    Args:
        packet: The packet, positioned at the payload.
        extra_ints: Ints between the body direction and the status string.

    Returns:
        The entries decoded before any problem, plus the problem (``None`` if the
        whole packet decoded with nothing left over).
    """
    statuses: list[UserStatus] = []
    try:
        count = _read_int(packet)
        if not 0 <= count <= 1000:
            return statuses, f"implausible count {count}"
        for n in range(count):
            index = _read_int(packet)
            x = _read_int(packet)
            y = _read_int(packet)
            z = _read_str(packet)
            head = _read_int(packet)
            body = _read_int(packet)
            for _ in range(extra_ints):
                _read_int(packet)
            status = _read_str(packet)
            problem = _implausible_status(index, x, y, z, head, body, status)
            if problem:
                return statuses, f"entry {n} of {count}: implausible {problem}"
            statuses.append(UserStatus(index, x, y, parse_status_actions(status)))
    except ProtocolError as exc:
        return statuses, f"entry {len(statuses)}: {exc}"
    if _remaining(packet):
        return statuses, f"{_remaining(packet)} unread bytes"
    return statuses, None


def parse_user_updates(packet: HPacket) -> tuple[list[UserStatus], str | None]:
    """Parse ``UserUpdate``.

    Layout: an int count, then per user ``i index, i x, i y, s z, i head dir,
    i body dir, [extra ints], s status``.

    Tries each known layout and takes the first that decodes the whole packet to
    plausible values. An older layout is only trusted when it fits the whole packet,
    since it can misread the extra int as an empty status and "decode" junk.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The entries, plus a description of the problem (``None`` if the packet parsed
        cleanly). If no layout fits, the entries are what the current layout decoded
        before the problem. Never raises.
    """
    start = packet.read_index
    attempts: list[tuple[list[UserStatus], str]] = []
    for extra in _USER_UPDATE_EXTRA_INTS:
        packet.read_index = start
        statuses, problem = _parse_user_update_entries(packet, extra)
        if problem is None:
            return statuses, None
        attempts.append((statuses, f"{problem} (assuming {extra} extra int(s))"))
    return attempts[0]


def parse_index_value(packet: HPacket) -> tuple[int, int]:
    """Parse incoming ``Dance`` (index, style) or ``Expression`` (index, type).

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The room index and the value.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    return _read_int(packet), _read_int(packet)


def parse_sleep(packet: HPacket) -> tuple[int, bool]:
    """Parse ``Sleep``: ``i index, B sleeping``.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The room index and whether that user is now asleep.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    index = _read_int(packet)
    if _remaining(packet) < 1:
        raise ProtocolError("truncated bool")
    return index, packet.read_bool()


def parse_floor_heightmap(packet: HPacket) -> frozenset[tuple[int, int]]:
    """Parse ``FloorHeightMap``: ``B small scale, i wall height, s rows``.

    Rows are separated by carriage returns; ``x`` marks a tile that isn't floor. The
    column is x and the row is y.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        Every floor tile.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    if _remaining(packet) < 1:
        raise ProtocolError("truncated bool")
    packet.read_bool()
    _read_int(packet)
    rows = re.split(r"\r\n|\r|\n", _read_str(packet))
    return frozenset(
        (x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch not in "xX"
    )


def parse_entry_tile(packet: HPacket) -> tuple[int, int]:
    """Parse ``RoomEntryTile``: ``i x, i y, i dir``.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The door tile; walking onto it leaves the room.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    return _read_int(packet), _read_int(packet)


def parse_room_ready(packet: HPacket) -> int:
    """Parse ``RoomReady``: ``s room model, i room id``.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The room id.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    _read_str(packet)
    return _read_int(packet)


def build_go_to_room(room_id: int) -> HPacket:
    """Build ``GetGuestRoom(room id, entering=0, forward=1)``.

    This is what the navigator sends to go to a room; the client then enters it
    normally.

    Args:
        room_id: The room to go to.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("GetGuestRoom", room_id, 0, 1)


def parse_int(packet: HPacket) -> int:
    """Parse a single-int outgoing action.

    Used for ``Dance``, ``ChangePosture``, ``AvatarExpression`` and ``Sign``.

    Args:
        packet: The packet, positioned at the payload.

    Returns:
        The int.

    Raises:
        ProtocolError: If the packet is truncated.
    """
    return _read_int(packet)


def build_move(x: int, y: int) -> HPacket:
    """Build ``MoveAvatar``: walk to a tile (the server finds the path).

    Args:
        x: Target x tile.
        y: Target y tile.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("MoveAvatar", x, y)


def build_look_to(x: int, y: int) -> HPacket:
    """Build ``LookTo``: turn to face a point.

    Args:
        x: X coordinate to face (may be far outside the room).
        y: Y coordinate to face.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("LookTo", x, y)


def build_dance(style: int) -> HPacket:
    """Build ``Dance``.

    Args:
        style: 0 stop, 1 normal, 2-4 Habbo Club dances.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("Dance", style)


def build_posture(posture: int) -> HPacket:
    """Build ``ChangePosture``.

    Args:
        posture: ``POSTURE_STAND`` or ``POSTURE_SIT``.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("ChangePosture", posture)


def build_expression(expression: int) -> HPacket:
    """Build ``AvatarExpression``.

    Args:
        expression: 1 wave, 2 blow kiss, 3 laugh, 6 jump, 7 thumbs up.

    Returns:
        The packet, addressed by name.
    """
    return HPacket("AvatarExpression", expression)


def build_sign(sign: int) -> HPacket:
    """Build ``Sign``.

    Args:
        sign: 0-17 (numbers 0-10, heart, skull, !, soccer ball, smile, red/yellow card).

    Returns:
        The packet, addressed by name.
    """
    return HPacket("Sign", sign)
