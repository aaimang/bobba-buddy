"""Habbo's own windows for the owner: notification bubbles and the Message of the Day.

These are incoming packets the bot makes up and sends to the owner's client with
``send_to_client``. Nothing goes to the server, so only the owner sees them.
Sends run on one ``buddy-notify`` thread, never on G-Python's callback thread.

The layout (``NotificationDialog``: ``s type, i[s key, s value]``) comes from
HabboPackets and was checked on the live AIR client (MAC63, 2026-10): bubbles
(``display=BUBBLE``) show an ``image`` but print HTML tags as text; popups
(``display=POP_UP``) show an ``image``, a ``linkUrl``/``linkTitle`` button, line
breaks and ``<b>``, ``<i>``, ``<u>`` and ``<font color>``. The Message of the Day
window is plain text only, so it isn't used. G-Earth needs Developer mode on to let
an extension send these.

Attributes:
    DIALOG_TYPE (str): The ``NotificationDialog`` type the bot uses.
    LOGO_URL (str): The Bobba Buddy logo (docs site), shown on every window.
    DOCS_URL (str): The docs, for the help popup's button.
    GREEN (str): Colour for good news in popups.
    RED (str): Colour for problems in popups.
    BUBBLE_MAX (int): Longest bubble text, in characters.
    PAGE_MAX (int): Longest popup text, in characters.
    QUEUE_MAX (int): Most notifications waiting to be sent; the oldest are dropped.
    SEND_GAP_S (float): Pause between two sends.
"""

from __future__ import annotations

import html
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from .gpython import Direction, HPacket
from .guard import guarded

log = logging.getLogger(__name__)

DIALOG_TYPE = "bobba_buddy"
LOGO_URL = "https://aaimang.github.io/bobba-buddy/_static/logo.png"
DOCS_URL = "https://aaimang.github.io/bobba-buddy/"
GREEN = "#2E7D32"
RED = "#C62828"
BUBBLE_MAX = 160
PAGE_MAX = 1500
QUEUE_MAX = 20
SEND_GAP_S = 0.3

_CONTROL = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def clean(text: str, limit: int, *, multiline: bool = False) -> str:
    """Remove control characters and cut text to a length.

    Args:
        text: The text.
        limit: Most characters to keep; longer text ends in ``…``.
        multiline: Keep line breaks (Message of the Day pages).

    Returns:
        The cleaned text.
    """
    text = text.replace("\r\n", "\n")
    if not multiline:
        text = text.replace("\n", " ")
    text = _CONTROL.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def escape(text: str) -> str:
    """Make text safe inside a popup, which reads ``<``, ``>`` and ``&`` as markup.

    Args:
        text: Plain text.

    Returns:
        The escaped text.
    """
    return html.escape(text, quote=True)


def green(text: str) -> str:
    """Popup markup for green text (``text`` must already be escaped).

    Args:
        text: The text.

    Returns:
        The markup.
    """
    return f'<font color="{GREEN}">{text}</font>'


def red(text: str) -> str:
    """Popup markup for red text (``text`` must already be escaped).

    Args:
        text: The text.

    Returns:
        The markup.
    """
    return f'<font color="{RED}">{text}</font>'


def build_dialog(params: dict[str, str], dialog_type: str = DIALOG_TYPE) -> HPacket:
    """Build an incoming ``NotificationDialog``.

    Args:
        params: The dialog's keys and values, e.g. ``display``, ``message``, ``title``.
        dialog_type: The dialog type string.

    Returns:
        The packet, addressed by name.
    """
    values: list[Any] = [dialog_type, len(params)]
    for key, value in params.items():
        values += [key, value]
    return HPacket("NotificationDialog", *values)


def _extras(image: str | None, link: tuple[str, str] | None) -> dict[str, str]:
    """The optional ``image`` and link keys of a dialog."""
    extra: dict[str, str] = {}
    if image:
        extra["image"] = image
    if link:
        extra["linkUrl"], extra["linkTitle"] = link
    return extra


def build_bubble(
    message: str,
    *,
    title: str = "Bobba Buddy",
    image: str | None = LOGO_URL,
    link: tuple[str, str] | None = None,
) -> HPacket:
    """A notification bubble (the small box at the top right that fades by itself).

    Args:
        message: Plain text (bubbles show HTML tags as they are).
        title: Shown before the text.
        image: Icon URL; the client also expands ``${image.library.url}``.
        link: ``(url, title)`` for a link.

    Returns:
        The packet.
    """
    text = clean(f"{title}: {message}" if title else message, BUBBLE_MAX)
    return build_dialog({"display": "BUBBLE", "message": text, **_extras(image, link)})


def build_popup(
    message: str,
    *,
    title: str = "Bobba Buddy",
    image: str | None = LOGO_URL,
    link: tuple[str, str] | None = None,
) -> HPacket:
    """A notification popup (a window with a title and an OK button).

    Args:
        message: The text; may use ``<b>``, ``<i>``, ``<u>`` and ``<font color>``, so
            escape anything that isn't yours with :func:`escape`.
        title: The window title.
        image: Picture URL; the client also expands ``${image.library.url}``.
        link: ``(url, title)`` for an extra button.

    Returns:
        The packet.
    """
    return build_dialog(
        {
            "display": "POP_UP",
            "title": clean(title, 60),
            "message": clean(message, PAGE_MAX, multiline=True),
            **_extras(image, link),
        }
    )


class HotelNotifier:
    """Sends windows to the owner's client, throttled, on its own thread.

    Attributes:
        KINDS (tuple[str, ...]): Notification kinds a config toggle can turn off.
    """

    KINDS = ("config", "cooldown", "error", "room")

    def __init__(
        self,
        ext: Any,
        *,
        enabled: Callable[[str], bool] = lambda kind: True,
        min_gap_s: Callable[[], float] = lambda: 10.0,
        fallback: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        gap_s: float = SEND_GAP_S,
    ) -> None:
        """Set up; call :meth:`start` to begin sending.

        Args:
            ext: The G-Python extension (or a test fake).
            enabled: Whether a kind may be shown now (reads the config).
            min_gap_s: Seconds before the same notification may show again.
            fallback: Called once per session if the client can't be reached, e.g.
                to write to the G-Earth console.
            clock: Monotonic clock; injectable for tests.
            gap_s: Pause between two sends.
        """
        self._ext = ext
        self._enabled = enabled
        self._min_gap_s = min_gap_s
        self._fallback = fallback
        self._clock = clock
        self._gap_s = gap_s
        self._queue: deque[HPacket] = deque(maxlen=QUEUE_MAX)
        self._last: dict[str, float] = {}
        self._cond = threading.Condition()
        self._stop = False
        self._failed = False
        self._thread: threading.Thread | None = None

    # --- what to show --------------------------------------------------------------

    def notify(self, kind: str, message: str, key: str | None = None) -> bool:
        """Queue a bubble, unless that kind is off or it was shown recently.

        Args:
            kind: One of :attr:`KINDS` (anything else is always allowed).
            message: The text.
            key: Identifies repeats; ``kind:message`` if not given.

        Returns:
            ``True`` if it was queued.
        """
        if kind in self.KINDS and not self._enabled(kind):
            return False
        if not self._enabled("in_hotel"):
            return False
        key = key or f"{kind}:{message}"
        now = self._clock()
        with self._cond:
            last = self._last.get(key)
            if last is not None and now - last < self._min_gap_s():
                return False
            self._last[key] = now
        return self.send(build_bubble(message))

    def send(self, packet: HPacket) -> bool:
        """Queue any client-bound packet (help, status and test windows use this).

        Args:
            packet: The packet.

        Returns:
            ``True`` (the queue drops its oldest entry when full).
        """
        with self._cond:
            self._queue.append(packet)
            self._cond.notify()
        return True

    def clear(self) -> None:
        """Forget queued sends and repeat timers (the game disconnected)."""
        with self._cond:
            self._queue.clear()
            self._last.clear()
            self._failed = False

    # --- the thread ----------------------------------------------------------------

    def start(self) -> None:
        """Start the ``buddy-notify`` thread."""
        if self._thread is None:
            self._thread = threading.Thread(
                target=guarded(self._run), name="buddy-notify", daemon=True
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the thread.

        Args:
            timeout: Seconds to wait for it.
        """
        with self._cond:
            self._stop = True
            self._cond.notify()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def flush(self) -> None:
        """Send everything queued now, on this thread (tests and shutdown)."""
        while True:
            with self._cond:
                if not self._queue:
                    return
                packet = self._queue.popleft()
            self._deliver(packet)

    def _run(self) -> None:
        """Send queued packets one by one until stopped."""
        while True:
            with self._cond:
                while not self._queue and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                packet = self._queue.popleft()
            self._deliver(packet)
            time.sleep(self._gap_s)

    def _deliver(self, packet: HPacket) -> None:
        """Send one packet; report once per session if the client can't take it.

        Args:
            packet: The packet.
        """
        name = packet.incomplete_identifier
        known = getattr(self._ext, "packet_infos", None) or {}
        ok = False
        if name in known.get(Direction.TO_CLIENT, {}):
            try:
                ok = self._ext.send_to_client(packet) is not False
            except Exception:
                log.debug("send_to_client failed", exc_info=True)
        if ok:
            log.debug("hotel window sent: %s", name)
            return
        log.info("couldn't show %s in the hotel", name)
        with self._cond:
            first, self._failed = not self._failed, True
        if first and self._fallback is not None:
            self._fallback(f"Couldn't show {name} in the hotel; messages stay here in the console.")
