"""Checks GitHub for a newer Bobba Buddy release (at most once a day).

The settings window shows a banner when one exists, and the G-Earth console gets
one notice per new version. Only the latest *release* counts (not every tag),
since that's the version the project asks people to use. Nothing here ever raises
into the bot: a failed check is logged and tried again the next day.

Attributes:
    REPO (str): The GitHub repository.
    LATEST_URL (str): GitHub's "latest release" API endpoint.
    CHECK_INTERVAL_S (float): Shortest time between automatic checks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)

REPO = "aaimang/bobba-buddy"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_INTERVAL_S = 24 * 3600

_installed: UpdateChecker | None = None


class UpdateError(Exception):
    """GitHub couldn't be asked, or its answer couldn't be read."""


def parse_version(text: str) -> tuple[int, ...]:
    """A version as numbers, for comparing (``"v1.10.0"`` > ``"1.9.2"``).

    A leading ``v`` and anything after ``-`` or ``+`` are ignored.

    Args:
        text: The version or tag.

    Returns:
        The numbers, e.g. ``(1, 10, 0)``.

    Raises:
        ValueError: If it doesn't start with a number.
    """
    core = re.split(r"[-+]", text.strip().lstrip("vV"), maxsplit=1)[0]
    numbers = [int(part) for part in core.split(".") if part.isdigit()]
    if not numbers:
        raise ValueError(f"not a version: {text!r}")
    return tuple(numbers)


@dataclass(frozen=True)
class Release:
    """The latest release on GitHub.

    Attributes:
        version: The version, e.g. ``"1.5.0"``.
        tag: The git tag, e.g. ``"v1.5.0"``.
        url: The release page.
        published_at: When it was first published (ISO 8601), or ``""``.
    """

    version: str
    tag: str
    url: str
    published_at: str = ""


def fetch_latest(timeout: float = 10.0) -> Release:
    """Ask GitHub for the latest release.

    Args:
        timeout: Seconds before giving up.

    Returns:
        The latest release.

    Raises:
        UpdateError: On network or HTTP errors (including the rate limit) or an
            unreadable answer.
    """
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"bobba-buddy/{__version__}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"), strict=False)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub answered {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"couldn't reach GitHub: {exc}") from exc
    except ValueError as exc:
        raise UpdateError("GitHub's answer wasn't valid JSON") from exc
    try:
        tag = str(data["tag_name"])
        parse_version(tag)
        return Release(
            version=tag.lstrip("vV"),
            tag=tag,
            url=str(data.get("html_url") or f"https://github.com/{REPO}/releases"),
            published_at=str(data.get("published_at") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise UpdateError("GitHub's answer had no usable tag") from exc


class UpdateChecker:
    """Remembers the latest release and decides when to ask again.

    Safe to use from any thread. State lives in a small JSON file so restarts
    don't re-check or re-announce.

    Attributes:
        current: The running version.
    """

    def __init__(
        self,
        state_path: Path | None,
        *,
        current: str = __version__,
        enabled: Callable[[], bool] = lambda: True,
        fetch: Callable[[], Release] = fetch_latest,
        clock: Callable[[], float] = time.time,
        interval_s: float = CHECK_INTERVAL_S,
    ) -> None:
        """Load any saved state.

        Args:
            state_path: Where to keep the state (``state/update_check.json``);
                ``None`` keeps it in memory only.
            current: The running version.
            enabled: Whether automatic checks are on (``[updates] check``).
            fetch: Asks GitHub; injectable for tests.
            clock: Unix time; injectable for tests.
            interval_s: Shortest time between automatic checks.
        """
        self.current = current
        self._path = state_path
        self._enabled = enabled
        self._fetch = fetch
        self._clock = clock
        self._interval_s = interval_s
        self._lock = threading.Lock()
        self._latest: Release | None = None
        self._checked_at = 0.0
        self._error = ""
        self._announced = ""
        self._dismissed = ""
        self._checking = False
        self._load()

    # --- state -------------------------------------------------------------------

    def _load(self) -> None:
        """Read the state file; a missing or broken file starts fresh."""
        if self._path is None or not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._checked_at = float(data.get("checked_at", 0.0))
            self._announced = str(data.get("announced", ""))
            self._dismissed = str(data.get("dismissed", ""))
            latest = data.get("latest")
            if latest:
                self._latest = Release(**latest)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("ignoring unreadable update state %s: %s", self._path, exc)

    def _save(self) -> None:
        """Write the state file atomically (temporary file, then rename)."""
        if self._path is None:
            return
        payload = {
            "checked_at": self._checked_at,
            "latest": asdict(self._latest) if self._latest else None,
            "announced": self._announced,
            "dismissed": self._dismissed,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("could not save update state: %s", exc)

    # --- reading -----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Whether automatic checks are on."""
        try:
            return bool(self._enabled())
        except Exception:
            return True

    @property
    def latest(self) -> Release | None:
        """The latest release seen, or ``None`` before the first check."""
        with self._lock:
            return self._latest

    @property
    def checked_at(self) -> float:
        """Unix time of the last successful check, or 0."""
        with self._lock:
            return self._checked_at

    @property
    def error(self) -> str:
        """Why the last check failed, or ``""``."""
        with self._lock:
            return self._error

    @property
    def checking(self) -> bool:
        """Whether a check is running right now."""
        with self._lock:
            return self._checking

    @property
    def available(self) -> bool:
        """Whether the latest release is newer than the running version."""
        latest = self.latest
        if latest is None:
            return False
        try:
            return parse_version(latest.version) > parse_version(self.current)
        except ValueError:
            return False

    @property
    def dismissed(self) -> bool:
        """Whether the user hid the banner for this latest version."""
        latest = self.latest
        with self._lock:
            return latest is not None and self._dismissed == latest.version

    # --- actions -----------------------------------------------------------------

    def check_now(self) -> bool:
        """Ask GitHub right away (the "Check now" button); never raises.

        Returns:
            ``True`` if GitHub answered.
        """
        with self._lock:
            if self._checking:
                return False
            self._checking = True
        try:
            release = self._fetch()
        except Exception as exc:  # UpdateError, or anything a test fake raises
            message = str(exc) or type(exc).__name__
            log.info("update check failed: %s", message)
            with self._lock:
                self._error = message
                self._checking = False
            return False
        with self._lock:
            self._latest = release
            self._checked_at = self._clock()
            self._error = ""
            self._checking = False
            self._save()
        log.info(
            "update check: latest %s, running %s%s",
            release.version,
            self.current,
            " (update available)" if self.available else "",
        )
        return True

    def maybe_check(self) -> bool:
        """Check if checks are on and the last one was over a day ago.

        Returns:
            ``True`` if a check ran and GitHub answered.
        """
        if not self.enabled:
            return False
        with self._lock:
            due = self._clock() - self._checked_at >= self._interval_s
        return self.check_now() if due else False

    def dismiss(self) -> None:
        """Hide the banner until a newer version than this one comes out."""
        latest = self.latest
        if latest is None:
            return
        with self._lock:
            self._dismissed = latest.version
            self._save()

    def announce(self, notice: Callable[[str, str], None]) -> bool:
        """Tell the G-Earth console about a new version, once per version.

        Args:
            notice: ``bot.notice(text, color)``.

        Returns:
            ``True`` if a notice was sent.
        """
        latest = self.latest
        if latest is None or not self.available:
            return False
        with self._lock:
            if self._announced == latest.version:
                return False
            self._announced = latest.version
            self._save()
        try:
            notice(
                f"Bobba Buddy {latest.version} is available (you have {self.current}). "
                "Press ▶ for details.",
                "blue",
            )
        except Exception:
            log.debug("update notice failed", exc_info=True)
        return True


def install(state_path: Path | None, **kwargs) -> UpdateChecker:
    """Create the process-wide checker (once; later calls return the same one).

    Args:
        state_path: Where to keep the state.
        **kwargs: Passed to :class:`UpdateChecker`.

    Returns:
        The checker.
    """
    global _installed
    if _installed is None:
        _installed = UpdateChecker(state_path, **kwargs)
    return _installed


def current() -> UpdateChecker | None:
    """The installed checker, or ``None``."""
    return _installed


def uninstall() -> None:
    """Forget the installed checker (tests)."""
    global _installed
    _installed = None
