"""Preview the settings window without G-Earth: ``python -m bobba_buddy.settings``.

It edits the real ``config.toml`` in ``--project-dir``; the ON/OFF switch and status
line are simulated, and console notices are printed instead.

Usage::

    uv run python -m bobba_buddy.settings --project-dir .
    uv run python -m bobba_buddy.settings --page Avatar
    uv run python -m bobba_buddy.settings --page "Extension Logs" --demo-logs
"""

from __future__ import annotations

import argparse
import logging
import queue
import signal
import sys
import threading
from pathlib import Path
from typing import Any


class PreviewBridge:
    """A stand-in for the running bot."""

    def __init__(self) -> None:
        """Start switched on, connected as nobody, nothing spent."""
        self.enabled = True

    def status(self) -> dict[str, Any]:
        """A fake status."""
        return {
            "enabled": self.enabled,
            "fatal_error": None,
            "own_name": "preview",
            "spent_usd": 0.0,
            "budget_usd": 1.0,
        }

    def set_enabled(self, on: bool) -> None:
        """Record the switch.

        Args:
            on: ``True`` for on.
        """
        self.enabled = on
        print(f"Bot {'ON' if on else 'OFF'}")

    def reload(self) -> None:
        """Nothing to reload in a preview."""
        print("config saved")

    def notice(self, text: str, color: str = "black") -> None:
        """Print a console notice.

        Args:
            text: The message.
            color: Ignored.
        """
        print(text)


DEMO_LINES = (
    ("MainThread", "bot", logging.INFO, "notice: Bobba Buddy loaded (model claude-haiku-5-5)"),
    ("Thread-3", "bot", logging.DEBUG, "q from Friend1 via chat: 'whats the weather in Helsinki'"),
    ("Thread-3", "bot", logging.DEBUG, "q from Friend1 queued (1 pending)"),
    ("buddy-worker_0", "bot", logging.DEBUG, "calling claude-haiku-5-5 for q (search on)"),
    (
        "buddy-worker_0",
        "bot",
        logging.INFO,
        "cmd=q asker=Friend1 mode=chat result=ok latency=3.4s in=2210 out=164 searches=1 "
        "cost=$0.01240",
    ),
    ("buddy-worker_0", "bot", logging.DEBUG, "reply to Friend1: 2 bubble(s) via chat"),
    ("buddy-outbox", "outbox", logging.DEBUG, "sent chat bubble (96 chars)"),
    ("buddy-outbox", "outbox", logging.DEBUG, "sent chat bubble (41 chars)"),
    ("buddy-avatar", "avatar", logging.INFO, "avatar autopilot: MoveAvatar (4, 7)"),
    ("buddy-avatar", "avatar", logging.INFO, "avatar mimic: Dance (1,)"),
    ("Thread-3", "guard", logging.WARNING, "Users: entry 3 didn't parse, skipped ahead"),
    ("buddy-worker_1", "llm", logging.ERROR, "Claude API overloaded (529), retrying 1/2"),
)


def demo_logs(stop: threading.Event) -> None:
    """Log sample lines from the bot's usual threads (``--demo-logs``), then keep ticking.

    Args:
        stop: Set to stop.
    """

    def emit(thread: str, source: str, level: int, message: str) -> None:
        def run() -> None:
            logging.getLogger(f"bobba_buddy.{source}").log(level, message)

        worker = threading.Thread(target=run, name=thread)
        worker.start()
        worker.join()

    logging.getLogger().setLevel(logging.DEBUG)
    for line in DEMO_LINES:
        emit(*line)
    tick = 0
    while not stop.wait(2.0):
        tick += 1
        emit("buddy-avatar", "avatar", logging.INFO, f"avatar autopilot: LookTo (demo {tick})")


def main(argv: list[str] | None = None) -> int:
    """Open the window and wait for it to close.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` if ``None``.

    Returns:
        The exit code.
    """
    parser = argparse.ArgumentParser(prog="bobba_buddy.settings", description=__doc__)
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None, help="config file path")
    parser.add_argument("--page", default=None, help="page to open on, e.g. Avatar")
    parser.add_argument(
        "--demo-logs", action="store_true", help="fill Extension Logs with sample lines"
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    project_dir = (args.project_dir or Path.cwd()).expanduser().resolve()
    config_path = (args.config or project_dir / "config.toml").expanduser().resolve()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.getLogger().setLevel(logging.INFO)
    from . import logbuffer

    logbuffer.install(project_dir / "logs" / "bobba-buddy.log")
    logging.getLogger("bobba_buddy.settings").info("settings preview started")
    if args.demo_logs:
        threading.Thread(target=demo_logs, args=(stop,), name="demo", daemon=True).start()

    from .window import run_window

    run_window(
        PreviewBridge(),
        config_path,
        project_dir / "config.example.toml",
        stop.is_set,
        queue.Queue(),
        page=args.page,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
