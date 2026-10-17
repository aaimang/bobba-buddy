"""Paced sending of chat bubbles, so the account doesn't trip Habbo's flood control.

Attributes:
    MAX_QUEUED (int): Most bubbles that may wait at once; extra bubbles are dropped.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .protocol import ChatMode

log = logging.getLogger(__name__)

MAX_QUEUED = 30


@dataclass(frozen=True)
class Bubble:
    """One chat bubble waiting to be sent.

    Attributes:
        mode: Chat, shout or whisper.
        text: The bubble text (already sanitized and within the length limit).
        whisper_target: Who to whisper to, or ``None`` for chat and shout.
        generation: ``RoomState.generation`` when queued; the bubble is dropped if the
            room has changed since.
    """

    mode: ChatMode
    text: str
    whisper_target: str | None
    generation: int  # RoomState.generation when queued; dropped if the room changed


class Outbox:
    """One sender thread. Waits ``interval`` between bubbles and honours flood/mute pauses."""

    def __init__(
        self,
        send: Callable[[Bubble], None],
        *,
        interval_s: Callable[[], float],
        current_generation: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create a stopped outbox.

        Args:
            send: Sends one bubble to the server.
            interval_s: Returns the gap between bubbles (read each time, so config
                reloads apply).
            current_generation: Returns the room generation, to drop stale bubbles.
            clock: Monotonic clock in seconds; injectable for tests.
        """
        self._send = send
        self._interval_s = interval_s
        self._current_generation = current_generation
        self._clock = clock
        self._queue: queue.Queue[Bubble | None] = queue.Queue()  # None wakes the thread to stop
        self._lock = threading.Lock()
        self._paused_until = 0.0
        self._last_sent = float("-inf")
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the sender thread (no-op if it's already running)."""
        if self._thread is None:
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="buddy-outbox", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the sender thread; unsent bubbles are abandoned.

        Args:
            timeout: Seconds to wait for the thread to finish.
        """
        self._stop.set()
        self._queue.put(None)
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def enqueue(self, bubbles: list[Bubble]) -> bool:
        """Queue bubbles to send in order.

        Args:
            bubbles: The bubbles of one reply.

        Returns:
            ``False`` (and nothing queued) if they'd overflow ``MAX_QUEUED``.
        """
        if self._queue.qsize() + len(bubbles) > MAX_QUEUED:
            log.warning("outbox full, dropping %d bubble(s)", len(bubbles))
            return False
        for bubble in bubbles:
            self._queue.put(bubble)
        return True

    def pause_for(self, seconds: float) -> None:
        """Hold all sending for a while (flood control or a moderator mute).

        Args:
            seconds: How long to pause from now; an existing longer pause is kept.
        """
        with self._lock:
            self._paused_until = max(self._paused_until, self._clock() + seconds)
        log.info("outbox paused for %.0fs", seconds)

    def clear(self) -> None:
        """Drop every queued bubble (on room change or disconnect)."""
        dropped = 0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            self._queue.task_done()
            dropped += 1
        if dropped:
            log.info("outbox cleared %d pending bubble(s)", dropped)

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until everything queued has been sent or dropped (used by tests).

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            ``True`` if the queue emptied in time.
        """
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    def _ready_at(self) -> float:
        """Clock time when the next bubble may go.

        Returns:
            The later of the pause end and the last send plus the interval.
        """
        with self._lock:
            return max(self._paused_until, self._last_sent + self._interval_s())

    def _run(self) -> None:
        """Sender loop: wait for a bubble, wait until allowed, send it."""
        while not self._stop.is_set():
            try:
                bubble = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if bubble is None:
                self._queue.task_done()
                continue
            try:
                while not self._stop.is_set():
                    wait = self._ready_at() - self._clock()
                    if wait <= 0:
                        break
                    self._wake.wait(min(wait, 0.5))
                    self._wake.clear()
                if self._stop.is_set():
                    return
                if bubble.generation != self._current_generation():
                    log.debug("dropping stale bubble from a previous room")
                    continue
                self._send(bubble)
                with self._lock:
                    self._last_sent = self._clock()
            except Exception:  # never let one bad send kill the sender thread
                log.exception("failed to send bubble")
            finally:
                self._queue.task_done()
