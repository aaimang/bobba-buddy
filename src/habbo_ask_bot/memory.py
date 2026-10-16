"""Short per-user conversation memory for /q.

Only plain question/answer text is kept, never thinking blocks, so dropping the oldest
turns never trips the API's preserved-thinking history check.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class _Thread:
    """One user's conversation.

    Attributes:
        turns: (question, answer) pairs, oldest first.
        last_used: Clock time of the last appended turn, used for expiry.
    """

    turns: deque[tuple[str, str]] = field(default_factory=deque)
    last_used: float = 0.0


class ConversationMemory:
    """Thread-safe store of each user's recent /q turns, keyed by lowercased name."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        """Create an empty memory.

        Args:
            clock: Monotonic clock in seconds; injectable for tests.
        """
        self._clock = clock
        self._lock = threading.Lock()
        self._threads: dict[str, _Thread] = {}

    def history(self, user: str, ttl_s: float) -> list[dict[str, str]]:
        """Messages to prepend to the user's next request.

        A thread idle for longer than ``ttl_s`` is deleted and treated as empty.

        Args:
            user: Habbo name (case-insensitive).
            ttl_s: Seconds of inactivity after which the thread expires.

        Returns:
            Alternating ``user`` / ``assistant`` messages, oldest first, or ``[]``.
        """
        key = user.lower()
        with self._lock:
            thread = self._threads.get(key)
            if thread is None:
                return []
            if self._clock() - thread.last_used > ttl_s:
                del self._threads[key]
                return []
            messages: list[dict[str, str]] = []
            for question, answer in thread.turns:
                messages.append({"role": "user", "content": question})
                messages.append({"role": "assistant", "content": answer})
            return messages

    def append(self, user: str, question: str, answer: str, max_turns: int) -> None:
        """Record one exchange, dropping the oldest turns beyond ``max_turns``.

        Args:
            user: Habbo name (case-insensitive).
            question: The user message exactly as sent to Claude.
            answer: Claude's reply text.
            max_turns: How many exchanges to keep; 0 or less stores nothing.
        """
        if max_turns <= 0:
            return
        key = user.lower()
        with self._lock:
            thread = self._threads.setdefault(key, _Thread())
            thread.turns.append((question, answer))
            while len(thread.turns) > max_turns:
                thread.turns.popleft()
            thread.last_used = self._clock()

    def reset(self, user: str) -> None:
        """Forget one user's conversation (``/q reset``).

        Args:
            user: Habbo name (case-insensitive).
        """
        with self._lock:
            self._threads.pop(user.lower(), None)

    def clear(self) -> None:
        """Forget every conversation."""
        with self._lock:
            self._threads.clear()
