"""Opens the settings window on the main thread when G-Earth's ▶ button is pressed.

Tk must run on the process's main thread (macOS insists), but G-Python raises the
``double_click`` event on a thread of its own. So the event only queues a request
(:meth:`SettingsHost.request_open`), and the main thread, which otherwise just waits
for the session to end, serves it (:meth:`SettingsHost.serve`). The bot keeps running
on its other threads while the window is open.

This module doesn't import ``tkinter``: on Linux it may not be installed, and the bot
must still start. The window is imported only when it's first opened.
"""

from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

log = logging.getLogger(__name__)

Done = Callable[[], bool]


class Bridge(Protocol):
    """What the window needs from the running bot."""

    def status(self) -> dict[str, Any]:
        """The bot's status (see :meth:`~bobba_buddy.bot.BobbaBuddy.status`)."""
        ...

    def set_enabled(self, on: bool) -> None:
        """Turn the bot on or off."""
        ...

    def reload(self) -> None:
        """Apply a saved config right away."""
        ...

    def notice(self, text: str, color: str = "black") -> None:
        """Print to the G-Earth console."""
        ...


@dataclass
class BotBridge:
    """The :class:`~bobba_buddy.settings.host.Bridge` for the real bot and config store.

    Attributes:
        bot: The running bot.
        store: The config store, reloaded after a save.
    """

    bot: Any
    store: Any

    def status(self) -> dict[str, Any]:
        """The bot's status."""
        return self.bot.status()

    def set_enabled(self, on: bool) -> None:
        """Turn the bot on or off.

        Args:
            on: ``True`` for on.
        """
        self.bot.set_enabled(on)

    def reload(self) -> None:
        """Re-read ``config.toml`` now."""
        self.store.reload_now()

    def notice(self, text: str, color: str = "black") -> None:
        """Print to the G-Earth console.

        Args:
            text: The message.
            color: G-Earth console colour.
        """
        self.bot.notice(text, color)


Runner = Callable[[Bridge, Path, Path | None, Done, "queue.Queue[None]"], None]


def _tk_runner() -> Runner:
    """The real window, imported on first use (this is where ``tkinter`` loads)."""
    from .window import run_window

    return run_window


class SettingsHost:
    """Queues open requests from any thread and runs the window on the main thread.

    Attributes:
        bridge: The current bot, set for each G-Earth session.
    """

    def __init__(
        self,
        config_path: Path,
        example_path: Path | None,
        *,
        runner: Callable[[], Runner] = _tk_runner,
    ) -> None:
        """Create the host.

        Args:
            config_path: ``config.toml``.
            example_path: ``config.example.toml``, used if ``config.toml`` is missing.
            runner: Returns the function that shows the window until it closes;
                injectable for tests.
        """
        self._config_path = config_path
        self._example_path = example_path
        self._runner = runner
        self._requests: queue.Queue[None] = queue.Queue()
        self.bridge: Bridge | None = None

    def request_open(self) -> None:
        """Ask for the window (any thread, never blocks). An open window is raised."""
        self._requests.put(None)

    def _drain(self) -> None:
        """Forget queued requests."""
        while True:
            try:
                self._requests.get_nowait()
            except queue.Empty:
                return

    def serve(self, done: Done, poll_s: float = 0.5) -> None:
        """Wait on the main thread until ``done()``, opening the window when asked.

        Args:
            done: Returns ``True`` when the session is over; an open window closes too.
            poll_s: How often ``done`` is checked while idle.
        """
        while not done():
            try:
                self._requests.get(timeout=poll_s)
            except queue.Empty:
                continue
            self._drain()
            self.open(done)

    def open(self, done: Done) -> None:
        """Show the window until it's closed or ``done()``; never raises.

        Args:
            done: Returns ``True`` to close the window from outside.
        """
        bridge = self.bridge
        if bridge is None:
            return
        try:
            self._runner()(bridge, self._config_path, self._example_path, done, self._requests)
        except ImportError as exc:
            log.warning("settings window unavailable: %s", exc)
            bridge.notice(
                "Settings window unavailable: Tkinter isn't installed "
                "(Linux: sudo apt install python3-tk).",
                "red",
            )
        except Exception as exc:
            if type(exc).__name__ == "TclError":
                log.warning("settings window unavailable: %s", exc)
                bridge.notice(f"Settings window unavailable: {exc}", "red")
            else:
                log.exception("settings window crashed")
                bridge.notice(f"Settings window closed after an error: {exc}", "red")
        self._drain()
