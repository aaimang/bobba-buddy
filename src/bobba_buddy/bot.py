"""Wires G-Earth packets to commands, Claude, and paced chat replies.

Threading rule: G-Python runs every intercept callback on one thread, and G-Earth holds
each packet until the callback returns. A slow callback freezes the game client, and an
exception kills that thread for good. So callbacks here only parse and enqueue, always
inside :func:`~bobba_buddy.guard.guarded`, and Claude calls run on a worker pool.

Attributes:
    EXTENSION_INFO (dict[str, str]): Title, description, version and author shown in
        G-Earth's Extensions tab.
    EXTENSION_SETTINGS (dict[str, bool]): G-Python extension flags (double-click
        trigger on, can be left and deleted).
    REQUIRED_PACKETS (dict[Direction, tuple[str, ...]]): Packet names the chat features
        need; missing ones are reported on connect.
    META_COOLDOWN_S (float): Cooldown for usage and reset replies.
    SELF_ECHO_WINDOW_S (float): How long the bot recognises its own bubbles if they
        come back through an intercept.
    MSG_BUSY (str): Reply when the queue is full or the API is busy.
    MSG_REFUSAL (str): Reply when Claude declines.
    MSG_ERROR (str): Reply for other errors.
    MSG_BUDGET (str): Reply when the daily budget is spent (once per user per day).
    MSG_TOO_LONG (str): Reply when the argument exceeds ``max_input_chars``.
    MSG_RESET (str): Reply to ``/q reset``.
"""

from __future__ import annotations

import json
import logging
import math
import random
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from pathlib import Path
from typing import Any

from . import __version__
from .access import AccessControl, Denial, DenyReason, Ticket, is_allowed
from .avatar import AVATAR_PACKETS, AvatarController
from .budget import Budget
from .commands import SPECS, ParsedCommand, parse_command, usage_text
from .config import Config
from .gpython import Direction, HMessage, HPacket
from .guard import guarded, report_bad_packet, reset_reports
from .llm import ReplyKind, Responder
from .memory import ConversationMemory
from .outbox import Bubble, Outbox
from .prompts import build_user_message, system_prompt
from .protocol import (
    ChatMode,
    RoomUser,
    UserKind,
    build_chat,
    build_go_to_room,
    build_shout,
    build_whisper,
    parse_incoming_chat,
    parse_outgoing_chat,
    parse_room_ready,
    parse_seconds,
    parse_user_object,
    parse_user_remove,
    parse_users,
)
from .room import RoomState
from .textfmt import sanitize, split_bubbles, ulen

log = logging.getLogger(__name__)

EXTENSION_INFO = {
    "title": "Bobba Buddy",
    "description": "Chat commands answered by Claude, plus avatar autopilot, copying and anti-idle",
    "version": __version__,
    "author": "Mang",
}
EXTENSION_SETTINGS = {"use_click_trigger": True, "can_leave": True, "can_delete": True}

REQUIRED_PACKETS = {
    Direction.TO_CLIENT: (
        "Chat",
        "Shout",
        "Whisper",
        "Users",
        "UserRemove",
        "RoomReady",
        "UserObject",
        "FloodControl",
    ),
    Direction.TO_SERVER: ("Chat", "Shout", "Whisper"),
}
META_COOLDOWN_S = 10.0  # usage/reset replies
SELF_ECHO_WINDOW_S = 10.0

MSG_BUSY = "I'm a bit busy right now, try again soon."
MSG_REFUSAL = "I can't help with that one."
MSG_ERROR = "Something went wrong with that one, sorry."
MSG_BUDGET = "I've hit my daily AI limit, back tomorrow!"
MSG_TOO_LONG = "That's a bit long, try a shorter one."
MSG_RESET = "Done, I've forgotten our chat."


@dataclass(frozen=True)
class Route:
    """Where a reply goes: the asker's chat mode, and the whisper target if whispering.

    Attributes:
        mode: The mode the command arrived in (replies mirror it).
        asker: Who sent the command.
        whisper_target: Who to whisper the reply to, or ``None``.
    """

    mode: ChatMode
    asker: str
    whisper_target: str | None = None


class BobbaBuddy:
    """The bot: chat commands, room tracking, room keeping, and the avatar controller.

    Attributes:
        room: Shared room state.
        access: Whitelist, cooldowns and in-flight tracking.
        memory: ``/q`` conversation memory.
        outbox: Paced chat sender.
        avatar: Copying, autopilot and anti-idle.
        enabled: Whether the bot is on (double-click in G-Earth toggles it).
        fatal_error: Set when the API rejects the key or model; the bot pauses until
            toggled.
        STAY_RESETS_ATTEMPTS_S: Staying in a room this long resets the
            ``return_on_exit`` attempt count.
    """

    def __init__(
        self,
        ext: Any,
        config: Callable[[], Config],
        responder: Responder,
        budget: Budget,
        *,
        executor: Executor | None = None,
        today: Callable[[], date] = lambda: datetime.now().date(),
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        last_room_path: Path | None = None,
    ) -> None:
        """Create the bot; call :meth:`install` then :meth:`start`.

        Args:
            ext: The G-Python extension (or a test fake).
            config: Returns the current config (hot-reloaded).
            responder: Answers commands (Claude, or a test fake).
            budget: Daily spend tracker.
            executor: Runs Claude calls; a thread pool of ``[limits] workers`` by
                default.
            today: Returns today's date; injectable for tests.
            clock: Monotonic clock in seconds; injectable for tests.
            rng: Random source for the avatar; injectable for tests.
            last_room_path: Where the last room id is saved for rejoining; ``None``
                disables saving.
        """
        self._ext = ext
        self._last_room_path = last_room_path
        self._rejoin_timer: threading.Timer | None = None
        self._return_timer: threading.Timer | None = None
        self._return_attempts = 0
        self._in_room = False
        self._entered_at = 0.0
        self._config = config
        self._responder = responder
        self._budget = budget
        self._today = today
        self._clock = clock

        self.room = RoomState(clock)
        self.access = AccessControl(clock)
        self.memory = ConversationMemory(clock)
        self.outbox = Outbox(
            self._send_bubble,
            interval_s=lambda: self._config().chat.send_interval_s,
            current_generation=lambda: self.room.generation,
            clock=clock,
        )
        limits = config().limits
        self._executor = executor or ThreadPoolExecutor(
            max_workers=limits.workers, thread_name_prefix="buddy-worker"
        )
        self._pending = 0
        self._pending_lock = threading.Lock()
        self._sent_texts: deque[tuple[float, str]] = deque(maxlen=20)
        self._sent_lock = threading.Lock()
        self._notified: set[str] = set()
        self.enabled = True
        self.fatal_error: str | None = None
        self.avatar = AvatarController(
            ext,
            config,
            self.room,
            notice=self.notice,
            notice_once=self.notice_once,
            is_enabled=lambda: self.enabled,
            clock=clock,
            rng=rng,
        )

    # --- setup ---------------------------------------------------------------------

    def install(self) -> None:
        """Register intercepts and events. Call before ``ext.start()``."""
        to_client, to_server = Direction.TO_CLIENT, Direction.TO_SERVER
        for mode in ChatMode:
            self._ext.intercept(
                to_client, guarded(partial(self._on_incoming_chat, mode)), mode.packet_name
            )
            self._ext.intercept(
                to_server, guarded(partial(self._on_outgoing_chat, mode)), mode.packet_name
            )
        self._ext.intercept(to_client, guarded(self._on_users), "Users")
        self._ext.intercept(to_client, guarded(self._on_user_remove), "UserRemove")
        self._ext.intercept(to_client, guarded(self._on_room_ready), "RoomReady")
        self._ext.intercept(to_client, guarded(self._on_room_closed), "CloseConnection")
        self._ext.intercept(to_client, guarded(self._on_user_object), "UserObject")
        self._ext.intercept(to_client, guarded(self._on_flood_control), "FloodControl")
        self._ext.intercept(to_client, guarded(self._on_flood_control), "RemainingMutePeriod")
        self.avatar.install()
        self._ext.on_event("connection_start", guarded(self._on_connection_start))
        self._ext.on_event("connection_end", guarded(self._on_connection_end))
        self._ext.on_event("double_click", guarded(self.toggle))

    def start(self, *, avatar_thread: bool = True) -> None:
        """Start the outbox and (optionally) the avatar worker.

        Args:
            avatar_thread: Start the avatar thread; tests drive it by hand instead.
        """
        self.outbox.start()
        if avatar_thread:
            self.avatar.start()

    def stop(self) -> None:
        """Cancel timers and stop every worker thread."""
        for timer in (self._rejoin_timer, self._return_timer):
            if timer is not None:
                timer.cancel()
        self.outbox.stop()
        self.avatar.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # --- owner console -------------------------------------------------------------

    def notice(self, text: str, color: str = "black") -> None:
        """Message for the owner only: G-Earth console and the log, never the room.

        Args:
            text: The message.
            color: G-Earth console colour, e.g. ``"green"``, ``"orange"``, ``"red"``.
        """
        log.info("notice: %s", text)
        try:
            self._ext.write_to_console(text, color)
        except Exception:  # socket already closed
            log.debug("could not write to G-Earth console", exc_info=True)

    def notice_once(self, key: str, text: str, color: str = "orange") -> None:
        """Like :meth:`notice`, but only once per key per G-Earth connection.

        Args:
            key: Identifies the notice.
            text: The message.
            color: G-Earth console colour.
        """
        if key not in self._notified:
            self._notified.add(key)
            self.notice(text, color)

    def toggle(self) -> None:
        """Turn the bot on or off (double-click); turning on clears a fatal error."""
        self.enabled = not self.enabled
        if self.enabled:
            self.fatal_error = None
        self.notice(f"Bot {'ON' if self.enabled else 'OFF'}", "green" if self.enabled else "red")

    # --- session and room events ---------------------------------------------------

    def _on_connection_start(self) -> None:
        """The game connected: reset state, report missing packets, start rejoining.

        Also asks the server for the owner's identity (``InfoRetrieve``) and prints
        the ready notices.
        """
        reset_reports()
        self.room.reset_session()
        self.outbox.clear()
        self.avatar.reset()
        self._notified.clear()
        info = getattr(self._ext, "connection_info", None) or {}
        client_type = info.get("client_type", "?")
        self.notice(
            f"Connected to {info.get('host', '?')} ({client_type}, "
            f"{info.get('hotel_version', '?')})",
            "blue",
        )
        if client_type not in ("FLASH", "?"):
            self.notice(f"This bot is built for Habbo AIR (FLASH), not {client_type}.", "red")

        packet_infos = getattr(self._ext, "packet_infos", None) or {}
        required = {d: list(names) for d, names in REQUIRED_PACKETS.items()}
        av = self._config().avatar
        if av.autopilot or av.mimic or av.anti_idle:
            for direction, names in AVATAR_PACKETS.items():
                required[direction] += [n for n in names if n not in required[direction]]
        missing = [
            f"{name} ({'in' if direction is Direction.TO_CLIENT else 'out'})"
            for direction, names in required.items()
            for name in names
            if name not in packet_infos.get(direction, {})
        ]
        if missing:
            self.notice(
                "Unknown packet names, some features won't work: " + ", ".join(missing), "red"
            )

        outgoing = packet_infos.get(Direction.TO_SERVER, {})
        if "InfoRetrieve" in outgoing:
            self._ext.send_to_server(HPacket("InfoRetrieve"))
        reload_name = self._config().room.room_reload_packet
        if reload_name and reload_name in outgoing:
            self._ext.send_to_server(HPacket(reload_name))
        if self._config().chat.require_seen_packets:
            self.notice(
                "Ready. Say anything in chat once (and shout/whisper once if you want "
                "those reply modes) so G-Earth lets the bot send them.",
                "green",
            )
        else:
            self.notice(
                "Ready. If replies or avatar actions don't show up, turn on Developer mode "
                "in G-Earth's Extra tab (or do that action once by hand; G-Earth then allows "
                "it until you close it).",
                "green",
            )
        self.avatar.startup_notice()
        room_cfg = self._config().room
        if room_cfg.rejoin_on_start:
            self._rejoin_timer = threading.Timer(
                room_cfg.rejoin_delay_s, guarded(self.rejoin_last_room)
            )
            self._rejoin_timer.daemon = True
            self._rejoin_timer.start()

    def _on_connection_end(self) -> None:
        """The game disconnected: clear state."""
        self.room.reset_session()
        self.outbox.clear()
        self.avatar.reset()
        self.notice("Disconnected from the hotel.", "orange")

    def _on_users(self, msg: HMessage) -> None:
        """Add the users in a ``Users`` packet, reporting any part that didn't parse.

        Args:
            msg: The intercepted ``Users``.
        """
        users, problem = parse_users(msg.packet)
        if problem:
            report_bad_packet("Users", msg.packet, problem)
        self.room.add_users(users)

    def _on_user_remove(self, msg: HMessage) -> None:
        """Forget a user who left.

        Args:
            msg: The intercepted ``UserRemove``.
        """
        self.room.remove_user(parse_user_remove(msg.packet))

    def _on_room_change(self, _msg: HMessage) -> None:
        """Clear room state, pending replies and queued avatar actions.

        Args:
            _msg: The intercepted packet (unused).
        """
        self.room.new_room()
        self.outbox.clear()
        self.avatar.reset()

    def _on_room_ready(self, msg: HMessage) -> None:
        """Entered a room: reset room state and remember the room for rejoining.

        Args:
            msg: The intercepted ``RoomReady``.
        """
        self._on_room_change(msg)
        self._cancel_return()
        self._in_room = True
        self._entered_at = self._clock()
        self._save_last_room(parse_room_ready(msg.packet))

    # --- going back after leaving a room ---------------------------------------------

    STAY_RESETS_ATTEMPTS_S = 60.0  # staying this long counts as a successful return

    def _on_room_closed(self, msg: HMessage) -> None:
        """Left a room: with ``return_on_exit``, schedule going back.

        Each retry waits longer (``return_delay_s`` times the attempt number), up to
        ``return_max_attempts`` in a row.

        Args:
            msg: The intercepted ``CloseConnection``.
        """
        self._on_room_change(msg)
        self._in_room = False
        cfg = self._config().room
        if not cfg.return_on_exit or not self.enabled:
            return
        if self._clock() - self._entered_at >= self.STAY_RESETS_ATTEMPTS_S:
            self._return_attempts = 0
        if self._return_attempts >= cfg.return_max_attempts:
            return
        self._cancel_return()
        delay = cfg.return_delay_s * (self._return_attempts + 1)
        self._return_timer = threading.Timer(delay, guarded(self.return_to_last_room))
        self._return_timer.daemon = True
        self._return_timer.start()

    def _cancel_return(self) -> None:
        """Cancel a scheduled return, if any."""
        if self._return_timer is not None:
            self._return_timer.cancel()
            self._return_timer = None

    def return_to_last_room(self) -> bool:
        """Go back to the last room, unless already in one.

        Called ``return_delay_s`` after leaving a room.

        Returns:
            ``True`` if a go-to-room request was sent.
        """
        cfg = self._config().room
        if self._in_room or not cfg.return_on_exit or not self.enabled:
            return False
        room_id = self._load_last_room()
        if room_id is None:
            return False
        self._return_attempts += 1
        if self._return_attempts >= cfg.return_max_attempts:
            self.notice(
                f"Going back to room {room_id} (last try of {cfg.return_max_attempts}; if it "
                "fails I'll stay out).",
                "orange",
            )
        else:
            self.notice(f"You left the room; going back to {room_id}.", "blue")
        self._ext.send_to_server(build_go_to_room(room_id))
        return True

    # --- rejoining the room after a restart ----------------------------------------

    def _save_last_room(self, room_id: int) -> None:
        """Save the room id to ``last_room_path``.

        Args:
            room_id: The room just entered.
        """
        if self._last_room_path is None:
            return
        try:
            self._last_room_path.parent.mkdir(parents=True, exist_ok=True)
            self._last_room_path.write_text(json.dumps({"room_id": room_id}))
        except OSError as exc:
            log.warning("could not save last room: %s", exc)

    def _load_last_room(self) -> int | None:
        """Read the saved room id.

        Returns:
            The room id, or ``None`` if there's none or the file is unreadable.
        """
        if self._last_room_path is None:
            return None
        try:
            return int(json.loads(self._last_room_path.read_text())["room_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def rejoin_last_room(self) -> bool:
        """Go back into the last room seen, unless a room has already loaded by itself.

        Called ``rejoin_delay_s`` after connecting, when ``rejoin_on_start`` is on.

        Returns:
            ``True`` if a go-to-room request was sent.
        """
        if self.room.user_count:
            log.info("room already loaded, not rejoining")
            return False
        room_id = self._load_last_room()
        if room_id is None:
            self.notice(
                "No saved room to rejoin yet; enter a room once and it'll be remembered.", "orange"
            )
            return False
        self.notice(f"Re-entering your last room ({room_id}) to load it.", "blue")
        self._ext.send_to_server(build_go_to_room(room_id))
        return True

    def _on_user_object(self, msg: HMessage) -> None:
        """Learn the owner's identity.

        Args:
            msg: The intercepted ``UserObject``.
        """
        user_id, name = parse_user_object(msg.packet)
        self.room.set_identity(user_id, name)
        self.notice(f"Logged in as {name}", "blue")

    def _on_flood_control(self, msg: HMessage) -> None:
        """Pause replies while the owner is flood-muted or muted by a moderator.

        Args:
            msg: The intercepted ``FloodControl`` or ``RemainingMutePeriod``.
        """
        seconds = parse_seconds(msg.packet)
        if seconds > 0:
            self.outbox.pause_for(seconds + self._config().chat.flood_margin_s)
            self.notice(f"Habbo muted chat for {seconds}s, replies paused.", "orange")

    # --- chat ----------------------------------------------------------------------

    def _on_incoming_chat(self, mode: ChatMode, msg: HMessage) -> None:
        """Handle a command from another player.

        Ignores ordinary chat, the owner's own echo, unknown users, pets and bots.

        Args:
            mode: Chat, shout or whisper.
            msg: The intercepted packet.
        """
        event = parse_incoming_chat(mode, msg.packet)
        command = parse_command(event.text, self._config().commands)
        if command is None:
            return
        if self.room.is_own_index(event.user_index, event.text) or self._is_own_reply(event.text):
            return  # the owner's own message echoed back; handled from the outgoing side
        user = self.room.user_at(event.user_index)
        if user is None:
            self.notice_once(
                "unknown-user",
                "Got a command from someone not in my user list. "
                "Re-enter the room so I can load it.",
            )
            return
        if user.kind is not UserKind.PLAYER:
            return
        target = user.name if mode is ChatMode.WHISPER else None
        self.handle(command, user.name, Route(mode, user.name, target), is_owner=False)

    def _on_outgoing_chat(self, mode: ChatMode, msg: HMessage) -> None:
        """Handle a command the owner typed (never blocked, so the room still sees it).

        Args:
            mode: Chat, shout or whisper.
            msg: The intercepted packet.
        """
        event = parse_outgoing_chat(mode, msg.packet)
        if self._is_own_reply(event.text):
            return  # one of the bot's own bubbles
        self.room.note_outgoing(mode, event.text, event.style, event.tail)
        command = parse_command(event.text, self._config().commands)
        if command is None:
            return
        owner = self.room.own_name or "owner"
        target = event.whisper_target if mode is ChatMode.WHISPER else None
        self.handle(command, owner, Route(mode, owner, target), is_owner=True)

    def handle(self, command: ParsedCommand, asker: str, route: Route, *, is_owner: bool) -> None:
        """Run the gate checks, then queue the command for a worker.

        Checks, cheapest first: bot on, no fatal error, whitelist, reply mode
        sendable, argument length, roast target, cooldowns, budget, queue size. Usage
        and reset requests are answered directly.

        Args:
            command: The parsed command.
            asker: Who sent it.
            route: Where the reply goes.
            is_owner: Whether the owner sent it (skips the whitelist).
        """
        cfg = self._config()
        if not self.enabled:
            return
        if self.fatal_error:
            self.notice_once(
                "fatal",
                f"Bot paused: {self.fatal_error}. Fix it, then "
                "double-click the extension to resume.",
                "red",
            )
            return
        if not is_owner and not is_allowed(asker, cfg.whitelist, self.room.own_name):
            log.info("ignoring %s from non-whitelisted %s", command.name, asker)
            return
        if self._reply_mode(route, cfg) is None:
            return  # can't answer in this mode yet; don't spend an API call

        if command.kind != "run":
            meta = self.access.acquire(asker, "_meta", META_COOLDOWN_S)
            if isinstance(meta, Ticket):
                self.access.release(meta)
                if command.kind == "reset":
                    self.memory.reset(asker)
                    self._reply(route, MSG_RESET, 1)
                else:
                    self._reply(route, usage_text(command.name, cfg.commands), 2)
            return

        if len(command.arg) > cfg.limits.max_input_chars:
            self._reply(route, MSG_TOO_LONG, 1)
            return

        cmd_cfg = cfg.commands[command.name]
        target: RoomUser | None = None
        if SPECS[command.name].arg_is_target:
            target_name = command.arg or asker
            target = self.room.find_player(target_name)
            if target is None:
                self._reply(route, f"I can't see {target_name} here.", 1)
                return
            if target.name.lower() in cmd_cfg.opt_out:
                self._reply(route, f"{target.name} opted out of roasts.", 1)
                return

        acquired = self.access.acquire(
            asker,
            command.name,
            cmd_cfg.cooldown_s,
            target=target.name if target else None,
            target_cooldown_s=cmd_cfg.target_cooldown_s,
        )
        if isinstance(acquired, Denial):
            if acquired.reason is DenyReason.TARGET_COOLDOWN and target is not None:
                wait = math.ceil(acquired.retry_in_s)
                self._reply(route, f"Give {target.name} a break, try again in {wait}s.", 1)
            else:
                log.info("%s from %s denied: %s", command.name, asker, acquired.reason.value)
            return
        ticket = acquired

        if not self._budget.can_spend():
            self.access.release(ticket, refund=True)
            if self._budget.first_denial_today(asker):
                self._reply(route, MSG_BUDGET, 1)
            self.notice_once(
                "budget",
                f"Daily budget (${cfg.limits.daily_budget_usd:.2f}) "
                "reached; answering again after midnight.",
            )
            return

        with self._pending_lock:
            busy = self._pending >= cfg.limits.max_pending
            if not busy:
                self._pending += 1
        if busy:
            self.access.release(ticket, refund=True)
            self._reply(route, MSG_BUSY, 1)
            return

        try:
            self._executor.submit(self._work, command, asker, route, target, ticket)
        except RuntimeError:  # executor already shut down
            with self._pending_lock:
                self._pending -= 1
            self.access.release(ticket, refund=True)

    def _work(
        self,
        command: ParsedCommand,
        asker: str,
        route: Route,
        target: RoomUser | None,
        ticket: Ticket,
    ) -> None:
        """Answer one command on a worker thread.

        Calls Claude, records the cost, logs the call, replies, and releases the
        ticket (refunding cooldowns for failures that weren't the user's fault).

        Args:
            command: The parsed command.
            asker: Who sent it.
            route: Where the reply goes.
            target: The roast target, if any.
            ticket: The access ticket to release.
        """
        started = time.monotonic()
        refund = True
        try:
            cfg = self._config()
            cmd_cfg = cfg.commands[command.name]
            spec = SPECS[command.name]
            user_message = build_user_message(
                command.name,
                asker=asker,
                arg=command.arg,
                today=self._today(),
                target_name=target.name if target else None,
                target_motto=target.motto if target else None,
            )
            history = self.memory.history(asker, cfg.memory.ttl_s) if spec.uses_memory else []
            reply = self._responder.respond(
                system=system_prompt(command.name, cmd_cfg.max_chars),
                messages=[*history, {"role": "user", "content": user_message}],
                web_search=cmd_cfg.web_search,
                claude=cfg.claude,
            )
            self._budget.record(reply.cost_usd)
            log.info(
                "cmd=%s asker=%s mode=%s result=%s latency=%.1fs in=%d out=%d searches=%d "
                "cost=$%.5f stop=%s request_id=%s",
                command.name,
                asker,
                route.mode.name.lower(),
                reply.kind.value,
                time.monotonic() - started,
                reply.input_tokens,
                reply.output_tokens,
                reply.searches,
                reply.cost_usd,
                reply.stop_reason,
                reply.request_id,
            )

            if reply.kind is ReplyKind.OK:
                refund = False
                if spec.uses_memory:
                    self.memory.append(asker, user_message, reply.text, cfg.memory.max_turns)
                self._reply(route, reply.text, cmd_cfg.max_bubbles)
            elif reply.kind is ReplyKind.REFUSAL:
                refund = False
                self._reply(route, MSG_REFUSAL, 1)
            elif reply.kind is ReplyKind.BUSY:
                self._reply(route, MSG_BUSY, 1)
            elif reply.kind is ReplyKind.FATAL:
                self.fatal_error = reply.error or "Claude API rejected the request"
                self.notice(
                    f"Claude API error, bot paused: {self.fatal_error}. Fix the key or "
                    "model, then double-click the extension to resume.",
                    "red",
                )
            else:
                self._reply(route, MSG_ERROR, 1)
        except Exception:
            log.exception("command %s from %s failed", command.name, asker)
        finally:
            self.access.release(ticket, refund=refund)
            with self._pending_lock:
                self._pending -= 1

    # --- sending -------------------------------------------------------------------

    def _reply_mode(self, route: Route, cfg: Config) -> ChatMode | None:
        """The mode a reply can actually be sent in.

        G-Earth (without Developer mode) blocks extension packets of a type it hasn't
        seen this session. With ``require_seen_packets``, shouts fall back to chat;
        whispers never fall back, since that would make a private answer public.

        Args:
            route: Where the reply should go.
            cfg: Current config.

        Returns:
            The mode to use, or ``None`` if the reply can't be sent yet.
        """
        mode = route.mode
        if mode is ChatMode.WHISPER and not route.whisper_target:
            return None
        if not cfg.chat.require_seen_packets:
            return mode
        if mode is ChatMode.SHOUT and not self.room.has_seen(ChatMode.SHOUT):
            mode = ChatMode.CHAT
        if self.room.has_seen(mode):
            return mode
        if mode is ChatMode.WHISPER:
            self.notice_once(
                "unseen-whisper",
                "Can't send whisper replies yet: whisper anyone once by hand so G-Earth allows it.",
            )
        else:
            self.notice_once(
                "unseen-chat",
                "Can't reply yet: say anything in chat once by hand "
                "so G-Earth allows the bot to chat.",
            )
        return None

    def _reply(self, route: Route, text: str, max_bubbles: int) -> None:
        """Sanitize, split into bubbles, and queue a reply.

        Args:
            route: Where the reply goes.
            text: The reply text (sanitized here).
            max_bubbles: Most bubbles to use.
        """
        cfg = self._config()
        text = sanitize(text)
        mode = self._reply_mode(route, cfg)
        if not text or mode is None:
            return

        limit = cfg.chat.bubble_limit
        target = route.whisper_target if mode is ChatMode.WHISPER else None
        if target:
            limit -= ulen(target) + 1  # the target name shares the 100-char budget
        bubbles = split_bubbles(text, max(limit, 20), max_bubbles)
        generation = self.room.generation
        self.outbox.enqueue([Bubble(mode, b, target, generation) for b in bubbles])

    def _send_bubble(self, bubble: Bubble) -> None:
        """Build and send one bubble (called by the outbox thread).

        Args:
            bubble: The bubble to send.
        """
        cfg = self._config()
        style = cfg.chat.bubble_style if cfg.chat.bubble_style >= 0 else self.room.own_style
        if bubble.mode is ChatMode.CHAT:
            packet = build_chat(bubble.text, style, self.room.chat_tail)
        elif bubble.mode is ChatMode.SHOUT:
            packet = build_shout(bubble.text, style)
        else:
            assert bubble.whisper_target is not None
            packet = build_whisper(bubble.whisper_target, bubble.text, style)
        with self._sent_lock:
            self._sent_texts.append((self._clock(), bubble.text))
        self._ext.send_to_server(packet)

    def _is_own_reply(self, text: str) -> bool:
        """Whether text is one of the bot's recent bubbles (seen again as an intercept or echo).

        Args:
            text: Chat text.

        Returns:
            ``True`` if the bot sent identical text within ``SELF_ECHO_WINDOW_S``.
        """
        now = self._clock()
        with self._sent_lock:
            return any(now - at <= SELF_ECHO_WINDOW_S and t == text for at, t in self._sent_texts)
