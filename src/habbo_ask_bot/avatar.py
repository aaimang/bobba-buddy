"""Avatar behaviour: autopilot (random actions), mimic (copying), and anti-idle.

Packet callbacks only update RoomState and queue actions (G-Python runs them all on one
thread that holds the packet). A single worker thread sends the queued actions, at least
``action_gap_s`` apart, with copied actions taking priority over autopilot ones.

G-Earth (without Developer mode) only lets an extension send packet types it has already
seen from the client since it was opened. By default the bot sends anyway and lets
G-Earth decide (Developer mode lifts the check); ``require_seen_packets`` makes it wait
until it has seen the owner do an action once.

Attributes:
    ACTION_PACKETS (tuple[str, ...]): Outgoing packets the avatar worker sends, and
        whose manual use by the owner pauses the autopilot.
    AVATAR_PACKETS (dict[Direction, tuple[str, ...]]): Every packet name the avatar
        features rely on, checked on connect.
    HOW_TO_UNLOCK (dict[str, str]): What the owner must do by hand for G-Earth to allow
        each action packet (used in notices).
    QUEUED_MAX_AGE_S (float): A queued action older than this is dropped as stale.
    OWN_SEND_WINDOW_S (float): How long a sent action is remembered, to recognise it if
        it comes back through the owner's outgoing intercept.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

from .config import AvatarConfig, Config
from .gpython import Direction, HMessage, HPacket
from .guard import guarded, report_bad_packet
from .protocol import (
    COPYABLE_EXPRESSIONS,
    EXPRESSION_IDLE,
    EXPRESSION_WAVE,
    FREE_EXPRESSIONS,
    LOOK_DIRECTIONS,
    MAX_SIGN,
    POSTURE_SIT,
    POSTURE_STAND,
    parse_entry_tile,
    parse_floor_heightmap,
    parse_index_value,
    parse_int,
    parse_sleep,
    parse_user_updates,
)
from .room import RoomState

log = logging.getLogger(__name__)

ACTION_PACKETS = ("MoveAvatar", "Dance", "ChangePosture", "AvatarExpression", "Sign", "LookTo")
AVATAR_PACKETS = {
    Direction.TO_CLIENT: (
        "UserUpdate",
        "Dance",
        "Expression",
        "Sleep",
        "FloorHeightMap",
        "RoomEntryTile",
    ),
    Direction.TO_SERVER: ACTION_PACKETS,
}
HOW_TO_UNLOCK = {
    "MoveAvatar": "walk somewhere",
    "Dance": "dance",
    "ChangePosture": "sit down",
    "AvatarExpression": "wave",
    "Sign": "hold up a sign",
    "LookTo": "click another avatar",
}
QUEUED_MAX_AGE_S = 5.0  # a copied action older than this is stale
OWN_SEND_WINDOW_S = 2.0
_TWO_INT_PACKETS = {"MoveAvatar", "LookTo"}


@dataclass(frozen=True)
class Action:
    """An avatar action waiting to be sent.

    Attributes:
        packet: Outgoing packet name, e.g. ``"Dance"``.
        args: The packet's int fields.
        reason: ``"mimic"``, ``"auto"`` or ``"wake"``.
        queued_at: Clock time it was queued, for staleness.
    """

    packet: str
    args: tuple[int, ...]
    reason: str  # "mimic" | "auto" | "wake"
    queued_at: float = 0.0

    def build(self) -> HPacket:
        """Build the packet to send.

        Returns:
            The packet, addressed by name.
        """
        return HPacket(self.packet, *self.args)


class AvatarController:
    """Copies nearby players, runs the autopilot, and keeps the owner awake."""

    def __init__(
        self,
        ext: Any,
        config: Callable[[], Config],
        room: RoomState,
        *,
        notice: Callable[[str, str], None],
        notice_once: Callable[[str, str, str], None],
        is_enabled: Callable[[], bool],
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        """Create a stopped controller.

        Args:
            ext: The G-Python extension (or a test fake).
            config: Returns the current config.
            room: Shared room state.
            notice: Writes a message to the owner's G-Earth console: ``(text, color)``.
            notice_once: Like ``notice`` but once per key: ``(key, text, color)``.
            is_enabled: Whether the bot is switched on (double-click toggles it).
            clock: Monotonic clock in seconds; injectable for tests.
            rng: Random source; injectable for repeatable tests.
        """
        self._ext = ext
        self._config = config
        self._room = room
        self._notice = notice
        self._notice_once = notice_once
        self._is_enabled = is_enabled
        self._clock = clock
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._queue: deque[Action] = deque(maxlen=4)
        self._last_action = float("-inf")
        self._paused_until = 0.0
        self._next_auto: float | None = None
        self._own_sends: deque[tuple[float, str, tuple[int, ...]]] = deque(maxlen=20)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    # --- lifecycle -----------------------------------------------------------------

    def install(self) -> None:
        """Register the avatar intercepts. Call before ``ext.start()``."""
        to_client, to_server = Direction.TO_CLIENT, Direction.TO_SERVER
        self._ext.intercept(to_client, guarded(self._on_user_update), "UserUpdate")
        self._ext.intercept(to_client, guarded(self._on_dance), "Dance")
        self._ext.intercept(to_client, guarded(self._on_expression), "Expression")
        self._ext.intercept(to_client, guarded(self._on_sleep), "Sleep")
        self._ext.intercept(to_client, guarded(self._on_floor), "FloorHeightMap")
        self._ext.intercept(to_client, guarded(self._on_entry_tile), "RoomEntryTile")
        for name in ACTION_PACKETS:
            self._ext.intercept(to_server, guarded(partial(self._on_owner_action, name)), name)

    def start(self) -> None:
        """Start the worker thread (no-op if it's already running)."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="askbot-avatar", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the worker thread.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def reset(self) -> None:
        """New connection or room: drop queued actions and restart the autopilot timer."""
        with self._lock:
            self._queue.clear()
            self._next_auto = None
            self._paused_until = 0.0

    def startup_notice(self) -> None:
        """Tell the owner which avatar features are on (and, in strict mode, what to unlock)."""
        av = self._config().avatar
        features = [
            name for name, on in (("autopilot", av.autopilot), ("copying", av.mimic)) if on
        ] + (["anti-idle"] if av.anti_idle else [])
        if not features:
            return
        if not av.require_seen_packets:
            self._notice(f"Avatar: {', '.join(features)} on.", "green")
            return
        needed = []
        if av.autopilot:
            needed += ["walk", "sit", "dance"]
        if av.mimic:
            needed += ["dance", "sit", "wave", "hold up a sign"]
        if av.anti_idle:
            needed += ["wave"]
        needed = list(dict.fromkeys(needed))
        self._notice(
            f"Avatar: {', '.join(features)} on. Do these once by hand so G-Earth lets the "
            f"bot do them: {', '.join(needed)}.",
            "green",
        )

    def _allowed(self, packet: str, av: AvatarConfig) -> bool:
        """Whether an action may be sent under the ``require_seen_packets`` setting.

        Args:
            packet: Outgoing packet name.
            av: Avatar settings.

        Returns:
            ``True`` in the default mode, or once the owner has sent this packet type.
        """
        return not av.require_seen_packets or self._room.has_seen(packet)

    # --- packet callbacks (packet thread: keep them quick) ---------------------------

    def _on_owner_action(self, name: str, msg: HMessage) -> None:
        """The owner's client sent an avatar action.

        Blocks the client's idle report (anti-idle). Otherwise records that G-Earth has
        seen the packet type and pauses the autopilot, unless it's the bot's own send
        coming back.

        Args:
            name: Outgoing packet name.
            msg: The intercepted message (may be blocked).
        """
        packet = msg.packet
        args = (
            (parse_int(packet), parse_int(packet))
            if name in _TWO_INT_PACKETS
            else (parse_int(packet),)
        )
        av = self._config().avatar
        if name == "AvatarExpression" and args[0] == EXPRESSION_IDLE:
            if av.anti_idle and self._is_enabled():
                msg.is_blocked = True  # the client reporting "I'm idle"; never let it through
                log.info("blocked the client's idle report")
            return
        if self._is_own_send(name, args):
            return
        self._room.note_seen(name)
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + av.owner_pause_s)

    def _on_user_update(self, msg: HMessage) -> None:
        """Update positions and copy nearby sit, stand and sign changes.

        Args:
            msg: The intercepted ``UserUpdate``.
        """
        statuses, problem = parse_user_updates(msg.packet)
        if problem:
            report_bad_packet("UserUpdate", msg.packet, problem)
        changes = self._room.apply_statuses(statuses)
        av = self._config().avatar
        if not changes or not (av.mimic and self._is_enabled()):
            return
        for change in changes:
            if not self._room.is_player_near_owner(change.index, av.mimic_radius):
                continue
            if change.kind in ("sit", "stand") and av.mimic_posture:
                posture = POSTURE_SIT if change.kind == "sit" else POSTURE_STAND
                self._enqueue(Action("ChangePosture", (posture,), "mimic"))
            elif change.kind == "sign" and av.mimic_signs and 0 <= (change.value or 0) <= MAX_SIGN:
                self._enqueue(Action("Sign", (change.value or 0,), "mimic"))

    def _on_dance(self, msg: HMessage) -> None:
        """Track dances and copy a nearby player's dance.

        Args:
            msg: The intercepted ``Dance``.
        """
        index, style = parse_index_value(msg.packet)
        self._room.set_dance(index, style)
        av = self._config().avatar
        if (
            av.mimic
            and av.mimic_dance
            and self._is_enabled()
            and self._room.is_player_near_owner(index, av.mimic_radius)
        ):
            self._enqueue(Action("Dance", (self._dance_style(style, av),), "mimic"))

    def _on_expression(self, msg: HMessage) -> None:
        """Copy a nearby player's expression (if the account can do it).

        Args:
            msg: The intercepted ``Expression``.
        """
        index, expression = parse_index_value(msg.packet)
        av = self._config().avatar
        if (
            expression in self._expressions(av)
            and av.mimic
            and av.mimic_expressions
            and self._is_enabled()
            and self._room.is_player_near_owner(index, av.mimic_radius)
        ):
            self._enqueue(Action("AvatarExpression", (expression,), "mimic"))

    def _on_sleep(self, msg: HMessage) -> None:
        """Wake the owner up when the server marks them asleep (anti-idle).

        Args:
            msg: The intercepted ``Sleep``.
        """
        index, sleeping = parse_sleep(msg.packet)
        if not sleeping or index != self._room.own_index:
            return
        av = self._config().avatar
        if not (av.anti_idle and self._is_enabled()):
            return
        action = self._wake_action(av)
        if action is None:
            self._notice_once(
                "wake-unavailable",
                "You went idle but the bot can't wake you yet: wave once by hand so G-Earth "
                "allows it.",
                "orange",
            )
            return
        log.info("idle detected, waking up with %s", action.packet)
        self._enqueue(action, front=True)

    def _on_floor(self, msg: HMessage) -> None:
        """Record the room's floor tiles.

        Args:
            msg: The intercepted ``FloorHeightMap``.
        """
        self._room.set_floor(parse_floor_heightmap(msg.packet))

    def _on_entry_tile(self, msg: HMessage) -> None:
        """Record the door tile.

        Args:
            msg: The intercepted ``RoomEntryTile``.
        """
        self._room.set_door(parse_entry_tile(msg.packet))

    # --- decisions -------------------------------------------------------------------

    def _enqueue(self, action: Action, *, front: bool = False) -> None:
        """Queue an action for the worker, skipping exact duplicates.

        Args:
            action: The action (its ``queued_at`` is set now).
            front: Put it first (wake-ups jump the queue).
        """
        action = replace(action, queued_at=self._clock())
        with self._lock:
            if any(q.packet == action.packet and q.args == action.args for q in self._queue):
                return
            if front:
                self._queue.appendleft(action)
            else:
                self._queue.append(action)
        self._wake.set()

    @staticmethod
    def _expressions(av: AvatarConfig) -> frozenset[int]:
        """Expressions this account can use.

        Args:
            av: Avatar settings.

        Returns:
            All copyable expressions with ``hc_expressions``, otherwise wave and thumbs up.
        """
        return COPYABLE_EXPRESSIONS if av.hc_expressions else FREE_EXPRESSIONS

    @staticmethod
    def _dance_style(style: int, av: AvatarConfig) -> int:
        """Map a dance to one this account can do.

        Args:
            style: Requested dance (clamped to 0-4).
            av: Avatar settings.

        Returns:
            The style, or 1 for Habbo Club dances without ``hc_dances``.
        """
        style = max(0, min(style, 4))
        return 1 if style > 1 and not av.hc_dances else style

    def _wake_action(self, av: AvatarConfig) -> Action | None:
        """Pick the first usable wake-up action from ``wake_actions``.

        Args:
            av: Avatar settings.

        Returns:
            A wave, a step to a free neighbouring tile, or a head turn; ``None`` if none
            is possible.
        """
        for choice in av.wake_actions:
            if choice == "wave" and self._allowed("AvatarExpression", av):
                return Action("AvatarExpression", (EXPRESSION_WAVE,), "wake")
            if choice == "walk" and self._allowed("MoveAvatar", av):
                tiles = self._room.free_neighbours()
                if tiles:
                    return Action("MoveAvatar", self._rng.choice(tiles), "wake")
            if choice == "look" and self._allowed("LookTo", av):
                own = self._room.own_position()
                if own is not None:
                    dx, dy = self._rng.choice([(1, 0), (-1, 0), (0, 1), (0, -1)])
                    return Action("LookTo", (own[0] + dx, own[1] + dy), "wake")
        return None

    def _autopilot_action(self, now: float, av: AvatarConfig) -> Action | None:
        """Pick a random autopilot action if one is due.

        The first call only schedules the timer. Actions are weighted by the
        ``*_weight`` settings; walking needs the floor map, and sitting is skipped
        while walking.

        Args:
            now: Current clock time.
            av: Avatar settings.

        Returns:
            The action, or ``None`` if it's not time, the autopilot is paused, or the
            owner's position is unknown.
        """
        if not av.autopilot:
            self._next_auto = None
            return None
        if self._next_auto is None:
            self._next_auto = now + self._rng.uniform(av.autopilot_min_s, av.autopilot_max_s)
            return None
        if now < self._next_auto or now < self._paused_until:
            return None
        self._next_auto = now + self._rng.uniform(av.autopilot_min_s, av.autopilot_max_s)

        own_index = self._room.own_index
        if own_index is None or self._room.own_position() is None:
            return None
        options: list[Action] = []
        weights: list[int] = []
        targets = self._room.walk_targets(av.walk_radius)
        if av.walk_weight and targets:
            options.append(Action("MoveAvatar", self._rng.choice(targets), "auto"))
            weights.append(av.walk_weight)
        if av.sit_weight and not self._room.is_moving(own_index):
            sitting = self._room.is_sitting(own_index)
            options.append(
                Action("ChangePosture", (POSTURE_STAND if sitting else POSTURE_SIT,), "auto")
            )
            weights.append(av.sit_weight)
        if av.dance_weight:
            dancing = self._room.dance_of(own_index)
            style = 0 if dancing else (self._rng.randint(1, 4) if av.hc_dances else 1)
            options.append(Action("Dance", (style,), "auto"))
            weights.append(av.dance_weight)
        if av.expression_weight:
            expression = self._rng.choice(sorted(self._expressions(av)))
            options.append(Action("AvatarExpression", (expression,), "auto"))
            weights.append(av.expression_weight)
        if av.sign_weight:
            options.append(Action("Sign", (self._rng.randint(0, MAX_SIGN),), "auto"))
            weights.append(av.sign_weight)
        if av.look_weight:
            options.append(Action("LookTo", self._rng.choice(LOOK_DIRECTIONS), "auto"))
            weights.append(av.look_weight)

        allowed = [
            (a, w) for a, w in zip(options, weights, strict=True) if self._allowed(a.packet, av)
        ]
        if not allowed:
            if options:
                missing = sorted({HOW_TO_UNLOCK[a.packet] for a in options})
                self._notice_once(
                    "autopilot-waiting",
                    f"Autopilot is waiting: {', '.join(missing)} once by hand so G-Earth "
                    "allows it.",
                    "orange",
                )
            return None
        actions, action_weights = zip(*allowed, strict=True)
        return self._rng.choices(actions, action_weights)[0]

    def _still_useful(self, action: Action) -> bool:
        """Skip actions that the current state already satisfies or that would fail.

        Args:
            action: The action about to be sent.

        Returns:
            ``False`` for sitting when already sitting or walking, standing when not
            sitting, the current dance again, or when the owner's index is unknown.
        """
        own_index = self._room.own_index
        if own_index is None:
            return False
        if action.packet == "ChangePosture":
            sitting = self._room.is_sitting(own_index)
            if action.args[0] == POSTURE_SIT:
                return not sitting and not self._room.is_moving(own_index)
            return sitting
        if action.packet == "Dance":
            return self._room.dance_of(own_index) != action.args[0]
        return True

    # --- worker ----------------------------------------------------------------------

    def step(self) -> Action | None:
        """Send at most one action if one is due. Called by the worker thread (and tests).

        Queued copies and wake-ups come first, then the autopilot. After an autopilot
        walk, a wave is queued when ``wave_on_walk`` is on.

        Returns:
            The action sent, or ``None``.
        """
        now = self._clock()
        av = self._config().avatar
        if not self._is_enabled():
            with self._lock:
                self._queue.clear()
            return None

        with self._lock:
            if now - self._last_action < av.action_gap_s:
                return None
            action = None
            while self._queue:
                candidate = self._queue.popleft()
                if now - candidate.queued_at <= QUEUED_MAX_AGE_S:
                    action = candidate
                    break
        if action is None:
            action = self._autopilot_action(now, av)
        if action is None or not self._still_useful(action):
            return None
        if not self._allowed(action.packet, av):
            self._notice_once(
                f"unseen-{action.packet}",
                f"To let the bot copy or do this, {HOW_TO_UNLOCK[action.packet]} once by hand "
                "(G-Earth only allows packets it has seen).",
                "orange",
            )
            return None

        with self._lock:
            self._last_action = now
            self._own_sends.append((now, action.packet, action.args))
            if action.reason == "mimic":
                self._paused_until = max(self._paused_until, now + av.mimic_pause_s)
        self._ext.send_to_server(action.build())
        log.info("avatar %s: %s %s", action.reason, action.packet, action.args)
        if action.packet == "MoveAvatar" and action.reason == "auto" and av.wave_on_walk:
            self._enqueue(Action("AvatarExpression", (EXPRESSION_WAVE,), "auto"))
        return action

    def _is_own_send(self, name: str, args: tuple[int, ...]) -> bool:
        """Whether an outgoing action is one the bot itself just sent.

        Args:
            name: Packet name.
            args: The packet's int fields.

        Returns:
            ``True`` if an identical action was sent within ``OWN_SEND_WINDOW_S``.
        """
        now = self._clock()
        with self._lock:
            return any(
                now - at <= OWN_SEND_WINDOW_S and n == name and a == args
                for at, n, a in self._own_sends
            )

    def _run(self) -> None:
        """Worker loop: call :meth:`step` every 0.25 s, or sooner when woken."""
        while not self._stop.is_set():
            try:
                self.step()
            except Exception:
                log.exception("avatar step failed")
            self._wake.wait(0.25)
            self._wake.clear()
