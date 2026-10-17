"""Preview the settings window without G-Earth: ``python -m bobba_buddy.settings``.

It edits the real ``config.toml`` in ``--project-dir``; the ON/OFF switch and status
line are simulated, and console notices are printed instead.

Usage::

    uv run python -m bobba_buddy.settings --project-dir .
    uv run python -m bobba_buddy.settings --page Avatar
"""

from __future__ import annotations

import argparse
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
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    project_dir = (args.project_dir or Path.cwd()).expanduser().resolve()
    config_path = (args.config or project_dir / "config.toml").expanduser().resolve()

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

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
