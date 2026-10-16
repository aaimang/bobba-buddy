"""Who may use the bot, and how often."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


def is_allowed(name: str, whitelist: frozenset[str], owner_name: str | None) -> bool:
    """Whether a user may run chat commands.

    Args:
        name: The user's Habbo name (case-insensitive).
        whitelist: Allowed names, already lowercased.
        owner_name: The logged-in account's name, which is always allowed; ``None`` if
            not known yet.

    Returns:
        ``True`` for whitelisted users and the owner.
    """
    lowered = name.lower()
    return lowered in whitelist or (owner_name is not None and lowered == owner_name.lower())


class DenyReason(Enum):
    """Why :meth:`AccessControl.acquire` refused a request.

    Attributes:
        COOLDOWN: The user ran this command too recently.
        TARGET_COOLDOWN: The ``/roast`` target was roasted too recently.
        IN_FLIGHT: The user already has a request being answered.
    """

    COOLDOWN = "cooldown"
    TARGET_COOLDOWN = "target_cooldown"
    IN_FLIGHT = "in_flight"


@dataclass(frozen=True)
class Denial:
    """A refused request.

    Attributes:
        reason: Why it was refused.
        retry_in_s: Seconds until the cooldown ends (0 for in-flight refusals).
    """

    reason: DenyReason
    retry_in_s: float = 0.0


@dataclass(frozen=True)
class Ticket:
    """Proof of an acquired slot; pass it back to :meth:`AccessControl.release`.

    Attributes:
        user: Lowercased user name.
        command: Internal command name.
        target: Lowercased roast target, or ``None``.
        used_at: Clock time the cooldowns were recorded, used to refund exactly them.
    """

    user: str
    command: str
    target: str | None
    used_at: float


class AccessControl:
    """Per-user command cooldowns, per-target cooldowns, and one request in flight per user.

    :meth:`acquire` checks and records atomically. If the request then fails for reasons
    that aren't the user's fault, ``release(refund=True)`` gives the cooldowns back.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty access tracker.

        Args:
            clock: Monotonic clock in seconds; injectable for tests.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._last_use: dict[tuple[str, str], float] = {}
        self._last_target: dict[str, float] = {}
        self._in_flight: set[str] = set()

    def acquire(
        self,
        user: str,
        command: str,
        cooldown_s: float,
        *,
        target: str | None = None,
        target_cooldown_s: float = 0.0,
    ) -> Ticket | Denial:
        """Check and record a request in one atomic step.

        Refused while the user has another request in flight, during the user's
        cooldown for this command, or during the target's cooldown. Spamming a target
        that's on cooldown still costs the asker their own cooldown.

        Args:
            user: The asker's name (case-insensitive).
            command: Internal command name; cooldowns are per command.
            cooldown_s: Per-user cooldown for this command.
            target: Roast target's name, if any.
            target_cooldown_s: Per-target cooldown.

        Returns:
            A :class:`Ticket` to release later, or a :class:`Denial`.
        """
        user_key = user.lower()
        target_key = target.lower() if target else None
        with self._lock:
            now = self._clock()
            if user_key in self._in_flight:
                return Denial(DenyReason.IN_FLIGHT)
            last = self._last_use.get((user_key, command))
            if last is not None and now - last < cooldown_s:
                return Denial(DenyReason.COOLDOWN, cooldown_s - (now - last))
            if target_key is not None:
                last_t = self._last_target.get(target_key)
                if last_t is not None and now - last_t < target_cooldown_s:
                    # Spamming one target still costs the asker their own cooldown.
                    self._last_use[(user_key, command)] = now
                    return Denial(DenyReason.TARGET_COOLDOWN, target_cooldown_s - (now - last_t))
                self._last_target[target_key] = now
            self._last_use[(user_key, command)] = now
            self._in_flight.add(user_key)
            return Ticket(user_key, command, target_key, now)

    def release(self, ticket: Ticket, *, refund: bool = False) -> None:
        """End a request.

        Args:
            ticket: The ticket from :meth:`acquire`.
            refund: Also undo the cooldowns this ticket recorded, for failures that
                weren't the user's fault (API busy, errors).
        """
        with self._lock:
            self._in_flight.discard(ticket.user)
            if not refund:
                return
            if self._last_use.get((ticket.user, ticket.command)) == ticket.used_at:
                del self._last_use[(ticket.user, ticket.command)]
            if ticket.target and self._last_target.get(ticket.target) == ticket.used_at:
                del self._last_target[ticket.target]

    def clear(self) -> None:
        """Forget every cooldown and in-flight request."""
        with self._lock:
            self._last_use.clear()
            self._last_target.clear()
            self._in_flight.clear()
