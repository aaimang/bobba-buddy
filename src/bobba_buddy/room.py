"""What the bot knows about the current room, the owner, and the G-Earth session.

Attributes:
    Tile: An ``(x, y)`` floor position.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .protocol import ChatMode, RoomUser, UserKind, UserStatus

_ECHO_WINDOW_S = 5.0

Tile = tuple[int, int]


@dataclass(frozen=True)
class StatusChange:
    """A posture or sign change seen in a UserUpdate.

    Attributes:
        index: Room index of the user.
        kind: ``"sit"``, ``"stand"`` or ``"sign"``.
        value: The sign number for ``"sign"``, otherwise ``None``.
    """

    index: int
    kind: str  # "sit" | "stand" | "sign"
    value: int | None = None


def chebyshev(a: Tile, b: Tile) -> int:
    """Tile distance where diagonals count as 1.

    So "within 1" means the 8 surrounding tiles.

    Args:
        a: First tile.
        b: Second tile.

    Returns:
        ``max(|dx|, |dy|)``.
    """
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class RoomState:
    """Thread-safe room snapshot, updated from the packet thread and read by workers.

    Holds three kinds of state:

    * **Session:** the owner's identity, and which outgoing packet types G-Earth has
      seen. Reset on each G-Earth connection.
    * **Room:** users, positions, postures, signs, dances, the floor map and the door.
      Reset on every room change.
    * **Chat details:** the owner's last chat style and tail int, and recent own texts
      used to recognise echoes.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty room state.

        Args:
            clock: Monotonic clock in seconds; injectable for tests.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._users: dict[int, RoomUser] = {}
        self._generation = 0
        self._own_id: int | None = None
        self._own_name: str | None = None
        self._own_index: int | None = None
        self._seen_outgoing: set[str] = set()
        self._chat_tail = -1
        self._own_style = 0
        self._recent_own_texts: deque[tuple[float, str]] = deque(maxlen=10)
        # Per-room avatar state
        self._positions: dict[int, Tile] = {}
        self._moving: set[int] = set()
        self._sitting: set[int] = set()
        self._signs: dict[int, int] = {}
        self._dances: dict[int, int] = {}
        self._floor: frozenset[Tile] = frozenset()
        self._door: Tile | None = None

    # --- session -----------------------------------------------------------------

    def reset_session(self) -> None:
        """New G-Earth connection: forget everything, including which packets G-Earth has seen."""
        with self._lock:
            self._own_id = self._own_name = None
            self._seen_outgoing.clear()
            self._chat_tail = -1
            self._recent_own_texts.clear()
            self._clear_room()

    def new_room(self) -> None:
        """Entered or left a room: users, positions and the floor map are no longer valid."""
        with self._lock:
            self._clear_room()

    def _clear_room(self) -> None:
        """Drop all per-room state and bump :attr:`generation` (caller holds the lock)."""
        self._users.clear()
        self._own_index = None
        self._generation += 1
        self._positions.clear()
        self._moving.clear()
        self._sitting.clear()
        self._signs.clear()
        self._dances.clear()
        self._floor = frozenset()
        self._door = None

    @property
    def generation(self) -> int:
        """Bumped on every room change, so stale queued replies can be dropped."""
        with self._lock:
            return self._generation

    # --- users -------------------------------------------------------------------

    def add_users(self, users: Iterable[RoomUser]) -> None:
        """Add or update users from a ``Users`` packet.

        Also learns the owner's room index if the owner is among them.

        Args:
            users: Parsed room users.
        """
        with self._lock:
            for user in users:
                self._users[user.index] = user
                self._positions[user.index] = (user.x, user.y)
                is_owner = user.kind is UserKind.PLAYER and user.id == self._own_id
                if self._own_id is not None and is_owner:
                    self._own_index = user.index

    def remove_user(self, index: int) -> None:
        """Forget a user who left the room.

        Args:
            index: The user's room index.
        """
        with self._lock:
            self._users.pop(index, None)
            self._positions.pop(index, None)
            self._moving.discard(index)
            self._sitting.discard(index)
            self._signs.pop(index, None)
            self._dances.pop(index, None)

    def user_at(self, index: int) -> RoomUser | None:
        """Look up a user by room index.

        Args:
            index: Room index.

        Returns:
            The user, or ``None`` if unknown.
        """
        with self._lock:
            return self._users.get(index)

    def find_player(self, name: str) -> RoomUser | None:
        """Find a player (not a pet or bot) by name.

        Args:
            name: Habbo name (case-insensitive, surrounding spaces ignored).

        Returns:
            The player, or ``None`` if nobody by that name is in the room.
        """
        lowered = name.strip().lower()
        with self._lock:
            for user in self._users.values():
                if user.kind is UserKind.PLAYER and user.name.lower() == lowered:
                    return user
        return None

    @property
    def user_count(self) -> int:
        """Number of known entities in the room (players, pets and bots)."""
        with self._lock:
            return len(self._users)

    # --- owner identity ----------------------------------------------------------

    def set_identity(self, user_id: int, name: str) -> None:
        """Record the owner's identity from ``UserObject``.

        Args:
            user_id: The owner's account id.
            name: The owner's Habbo name.
        """
        with self._lock:
            self._own_id, self._own_name = user_id, name
            for user in self._users.values():
                if user.kind is UserKind.PLAYER and user.id == user_id:
                    self._own_index = user.index

    @property
    def own_name(self) -> str | None:
        """The owner's Habbo name, once ``UserObject`` has been seen, else ``None``."""
        with self._lock:
            return self._own_name

    @property
    def own_index(self) -> int | None:
        """The owner's room index in the current room, or ``None`` if unknown."""
        with self._lock:
            return self._own_index

    def is_own_index(self, index: int, text: str) -> bool:
        """Whether an incoming chat line is the owner's own message echoed back.

        Uses the known own index, falling back to matching text the owner just sent
        (which also teaches us the own index if UserObject was missed).

        Args:
            index: Room index of the speaker.
            text: The chat text.

        Returns:
            ``True`` if the line is the owner's echo.
        """
        with self._lock:
            if self._own_index is not None:
                return index == self._own_index
            now = self._clock()
            for sent_at, sent_text in self._recent_own_texts:
                if now - sent_at <= _ECHO_WINDOW_S and sent_text == text:
                    self._own_index = index
                    return True
            return False

    # --- packets G-Earth has seen from the client ---------------------------------

    def note_outgoing(self, mode: ChatMode, text: str, style: int, tail: int | None) -> None:
        """Record chat the owner just sent: G-Earth has now seen this packet type.

        Args:
            mode: Chat, shout or whisper.
            text: The message text (without a whisper target).
            style: The owner's bubble style, reused for replies.
            tail: ``Chat``'s trailing int, reused in the bot's own ``Chat`` packets.
        """
        with self._lock:
            self._seen_outgoing.add(mode.packet_name)
            self._own_style = style
            if tail is not None:
                self._chat_tail = tail
            self._recent_own_texts.append((self._clock(), text))

    def note_seen(self, packet_name: str) -> None:
        """Record that the owner's client sent a packet type (G-Earth now allows it).

        Args:
            packet_name: Outgoing packet name, e.g. ``"Dance"``.
        """
        with self._lock:
            self._seen_outgoing.add(packet_name)

    def has_seen(self, packet: ChatMode | str) -> bool:
        """Whether the owner's client has sent this packet type this session.

        Args:
            packet: A chat mode or an outgoing packet name.

        Returns:
            ``True`` if seen.
        """
        name = packet.packet_name if isinstance(packet, ChatMode) else packet
        with self._lock:
            return name in self._seen_outgoing

    @property
    def chat_tail(self) -> int:
        """The trailing int of the owner's last ``Chat`` packet (default -1)."""
        with self._lock:
            return self._chat_tail

    @property
    def own_style(self) -> int:
        """The owner's last chat bubble style."""
        with self._lock:
            return self._own_style

    # --- positions, postures, dances ---------------------------------------------

    def apply_statuses(self, statuses: Iterable[UserStatus]) -> list[StatusChange]:
        """Update positions from a UserUpdate and report sit/stand/sign transitions.

        Args:
            statuses: Parsed ``UserUpdate`` entries.

        Returns:
            The changes since the previous update, for copying.
        """
        changes: list[StatusChange] = []
        with self._lock:
            for status in statuses:
                i = status.index
                self._positions[i] = (status.x, status.y)
                if status.moving:
                    self._moving.add(i)
                else:
                    self._moving.discard(i)
                was_sitting = i in self._sitting
                if status.sitting and not was_sitting:
                    self._sitting.add(i)
                    changes.append(StatusChange(i, "sit"))
                elif not status.sitting and was_sitting:
                    self._sitting.discard(i)
                    changes.append(StatusChange(i, "stand"))
                sign = status.sign
                if sign is not None and self._signs.get(i) != sign:
                    changes.append(StatusChange(i, "sign", sign))
                if sign is None:
                    self._signs.pop(i, None)
                else:
                    self._signs[i] = sign
        return changes

    def set_dance(self, index: int, style: int) -> None:
        """Record a ``Dance`` packet.

        Args:
            index: Room index of the dancer.
            style: Dance style; 0 means stopped.
        """
        with self._lock:
            if style:
                self._dances[index] = style
            else:
                self._dances.pop(index, None)

    def dance_of(self, index: int) -> int:
        """Current dance of a user.

        Args:
            index: Room index.

        Returns:
            The dance style, or 0 if not dancing.
        """
        with self._lock:
            return self._dances.get(index, 0)

    def is_sitting(self, index: int) -> bool:
        """Whether a user is sitting.

        Args:
            index: Room index.

        Returns:
            ``True`` if the last status had ``sit``.
        """
        with self._lock:
            return index in self._sitting

    def is_moving(self, index: int) -> bool:
        """Whether a user is walking.

        Args:
            index: Room index.

        Returns:
            ``True`` if the last status had ``mv``.
        """
        with self._lock:
            return index in self._moving

    def position_of(self, index: int) -> Tile | None:
        """Last known tile of a user.

        Args:
            index: Room index.

        Returns:
            The tile, or ``None`` if unknown.
        """
        with self._lock:
            return self._positions.get(index)

    def own_position(self) -> Tile | None:
        """The owner's last known tile.

        Returns:
            The tile, or ``None`` if the owner's index or position is unknown.
        """
        with self._lock:
            if self._own_index is None:
                return None
            return self._positions.get(self._own_index)

    def is_player_near_owner(self, index: int, radius: int) -> bool:
        """Whether another player (not a pet or bot) is within ``radius`` tiles of the owner.

        Args:
            index: Room index of the other user.
            radius: Maximum Chebyshev distance.

        Returns:
            ``False`` for the owner, pets, bots, and anyone with an unknown position.
        """
        with self._lock:
            own = self._own_index
            user = self._users.get(index)
            if own is None or index == own or user is None or user.kind is not UserKind.PLAYER:
                return False
            mine, theirs = self._positions.get(own), self._positions.get(index)
            return mine is not None and theirs is not None and chebyshev(mine, theirs) <= radius

    # --- floor map and walking ----------------------------------------------------

    def set_floor(self, tiles: frozenset[Tile]) -> None:
        """Record the room's floor tiles from ``FloorHeightMap``.

        Args:
            tiles: Every tile that is floor.
        """
        with self._lock:
            self._floor = tiles

    def set_door(self, tile: Tile) -> None:
        """Record the door tile from ``RoomEntryTile``.

        Args:
            tile: The door; walking onto it leaves the room.
        """
        with self._lock:
            self._door = tile

    def walk_targets(self, radius: int) -> list[Tile]:
        """Floor tiles the owner could walk to.

        Excludes the door (walking onto it leaves the room), dead-end tiles with one
        floor neighbour (how doors look when RoomEntryTile was missed), tiles with
        someone on them, and the owner's own tile.

        Args:
            radius: Maximum distance from the owner; 0 means anywhere.

        Returns:
            Sorted candidate tiles, or ``[]`` if the owner's position or the floor map
            is unknown.
        """
        with self._lock:
            own = self._positions.get(self._own_index) if self._own_index is not None else None
            if own is None or not self._floor:
                return []
            occupied = set(self._positions.values())
            floor = self._floor
            targets = []
            for tile in floor:
                if tile == self._door or tile in occupied:
                    continue
                if radius > 0 and chebyshev(tile, own) > radius:
                    continue
                x, y = tile
                neighbours = sum(
                    n in floor for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                )
                if neighbours <= 1:
                    continue
                targets.append(tile)
            return sorted(targets)

    def free_neighbours(self) -> list[Tile]:
        """Floor tiles right next to the owner that nobody is standing on.

        Returns:
            Sorted tiles among the 8 around the owner, excluding the door.
        """
        with self._lock:
            own = self._positions.get(self._own_index) if self._own_index is not None else None
            if own is None:
                return []
            occupied = set(self._positions.values())
            return sorted(
                (own[0] + dx, own[1] + dy)
                for dx in (-1, 0, 1)
                for dy in (-1, 0, 1)
                if (dx or dy)
                and (own[0] + dx, own[1] + dy) in self._floor
                and (own[0] + dx, own[1] + dy) not in occupied
                and (own[0] + dx, own[1] + dy) != self._door
            )
