"""Keeps the latest log lines in memory for the settings window's Extension Logs page.

:func:`install` adds a :class:`LogBuffer` handler to the root logger when the bot
starts, so the page can show lines from before it was opened. The buffer holds the
same lines that go to ``logs/bobba-buddy.log``; nothing new is written to disk.

Attributes:
    MAX_LINES (int): Lines kept; older ones drop off.
"""

from __future__ import annotations

import itertools
import logging
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

MAX_LINES = 2000

_installed: LogBuffer | None = None


@dataclass(frozen=True)
class LogLine:
    """One log record, ready to show.

    Attributes:
        seq: Increasing number, so readers can ask for "everything after N".
        created: Unix time of the record.
        level: Numeric level (``logging.INFO`` and so on).
        levelname: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR`` or ``CRITICAL``.
        thread: Name of the thread that logged it.
        source: Short logger name, e.g. ``bot`` for ``bobba_buddy.bot``.
        message: The message, with any traceback appended.
    """

    seq: int
    created: float
    level: int
    levelname: str
    thread: str
    source: str
    message: str


class LogBuffer(logging.Handler):
    """A logging handler that keeps the last :data:`MAX_LINES` records in memory.

    ``emit`` may run on any thread; reads take the same lock.

    Attributes:
        log_file: The log file on disk, for the page's "Open log file" button.
        base_level: The root logger's level when installed, restored when Debug
            is switched off.
    """

    def __init__(self, maxlen: int = MAX_LINES, log_file: Path | None = None) -> None:
        """Create an empty buffer.

        Args:
            maxlen: Lines kept.
            log_file: The log file on disk, if any.
        """
        super().__init__(logging.NOTSET)
        self._lines: deque[LogLine] = deque(maxlen=maxlen)
        self._counter = itertools.count(1)
        self._buffer_lock = threading.Lock()
        self.log_file = log_file
        self.base_level = logging.INFO

    def emit(self, record: logging.LogRecord) -> None:
        """Store one record; never raises.

        Args:
            record: The log record.
        """
        try:
            message = record.getMessage()
            if record.exc_info:
                message += "\n" + logging.Formatter().formatException(record.exc_info)
            name = record.name.removeprefix("bobba_buddy.")
            with self._buffer_lock:
                self._lines.append(
                    LogLine(
                        seq=next(self._counter),
                        created=record.created,
                        level=record.levelno,
                        levelname=record.levelname,
                        thread=record.threadName or "",
                        source=name.rsplit(".", 1)[-1] if name else "root",
                        message=message,
                    )
                )
        except Exception:  # a log call must never break the bot
            self.handleError(record)

    def since(self, seq: int) -> list[LogLine]:
        """The lines newer than ``seq``, oldest first.

        Args:
            seq: The last line already seen; 0 for everything kept.

        Returns:
            The newer lines.
        """
        with self._buffer_lock:
            return [line for line in self._lines if line.seq > seq]

    @property
    def last_seq(self) -> int:
        """The newest line's number, or 0 if there are none."""
        with self._buffer_lock:
            return self._lines[-1].seq if self._lines else 0

    def __len__(self) -> int:
        """Lines currently kept."""
        with self._buffer_lock:
            return len(self._lines)

    @property
    def debug(self) -> bool:
        """Whether the root logger is at DEBUG level."""
        return logging.getLogger().level == logging.DEBUG

    def set_debug(self, on: bool) -> None:
        """Turn DEBUG logging on or off for the running bot (back to normal on restart).

        Args:
            on: ``True`` for DEBUG, ``False`` for the level it started with.
        """
        logging.getLogger().setLevel(logging.DEBUG if on else self.base_level)
        logging.getLogger("bobba_buddy.settings").info("debug logging %s", "on" if on else "off")


def install(log_file: Path | None = None, maxlen: int = MAX_LINES) -> LogBuffer:
    """Add the buffer to the root logger (once; later calls return the same one).

    Args:
        log_file: The log file on disk, for "Open log file".
        maxlen: Lines kept.

    Returns:
        The installed buffer.
    """
    global _installed
    if _installed is None:
        _installed = LogBuffer(maxlen, log_file)
        _installed.base_level = logging.getLogger().level or logging.WARNING
        logging.getLogger().addHandler(_installed)
    elif log_file is not None:
        _installed.log_file = log_file
    return _installed


def current() -> LogBuffer | None:
    """The installed buffer, or ``None`` if :func:`install` hasn't run."""
    return _installed


def uninstall() -> None:
    """Remove the buffer from the root logger (tests)."""
    global _installed
    if _installed is not None:
        logging.getLogger().removeHandler(_installed)
        _installed = None
