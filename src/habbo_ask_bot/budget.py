"""Daily spend cap, persisted so a restart doesn't reset it."""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .config import PricingConfig

log = logging.getLogger(__name__)


def estimate_cost(usage: Any, pricing: PricingConfig) -> float:
    """USD cost of one response from its ``usage`` block.

    Cache writes bill at 1.25x input and cache reads at 0.1x; each web search is a
    flat fee.

    Args:
        usage: The response's ``usage`` object (or ``None``).
        pricing: Per-token and per-search prices.

    Returns:
        The estimated cost in USD.
    """
    if usage is None:
        return 0.0
    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    server_tool_use = getattr(usage, "server_tool_use", None)
    searches = (getattr(server_tool_use, "web_search_requests", 0) or 0) if server_tool_use else 0
    weighted_input = input_tokens + cache_write * 1.25 + cache_read * 0.1
    return (
        weighted_input * pricing.input_per_mtok / 1_000_000
        + output_tokens * pricing.output_per_mtok / 1_000_000
        + searches * pricing.per_search
    )


class Budget:
    """Tracks today's spend in a JSON file and resets at local midnight."""

    def __init__(
        self,
        state_path: Path,
        limit_usd: Callable[[], float],
        *,
        today: Callable[[], date] = lambda: datetime.now().date(),
    ) -> None:
        """Load today's total from ``state_path`` if it's from today.

        Args:
            state_path: JSON file holding ``date``, ``spent_usd`` and ``requests``.
            limit_usd: Returns the current daily cap (0 = no cap); read on every check
                so config reloads apply.
            today: Returns today's date; injectable for tests.
        """
        self._path = state_path
        self._limit_usd = limit_usd
        self._today = today
        self._lock = threading.Lock()
        self._day = today()
        self._spent = 0.0
        self._requests = 0
        self._notified: set[str] = set()
        self._load()

    def _load(self) -> None:
        """Read the state file; a missing or corrupt file means nothing spent."""
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable budget state %s: %s", self._path, exc)
            return
        if data.get("date") == self._day.isoformat():
            self._spent = float(data.get("spent_usd", 0.0))
            self._requests = int(data.get("requests", 0))

    def _save(self) -> None:
        """Write the state file atomically (temporary file, then rename)."""
        payload = {
            "date": self._day.isoformat(),
            "spent_usd": round(self._spent, 6),
            "requests": self._requests,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self._path)
        except OSError as exc:
            log.error("could not save budget state: %s", exc)

    def _roll(self) -> None:
        """Start a fresh day if the date has changed since the last call."""
        today = self._today()
        if today != self._day:
            self._day, self._spent, self._requests = today, 0.0, 0
            self._notified.clear()

    def can_spend(self) -> bool:
        """Whether another request fits today's cap.

        Returns:
            ``True`` if there's no cap or today's spend is still below it.
        """
        with self._lock:
            self._roll()
            limit = self._limit_usd()
            return limit <= 0 or self._spent < limit

    def record(self, cost_usd: float) -> None:
        """Add one request's cost to today's total and save it.

        Args:
            cost_usd: The request's cost; negative values count as 0.
        """
        with self._lock:
            self._roll()
            self._spent += max(cost_usd, 0.0)
            self._requests += 1
            self._save()

    def first_denial_today(self, user: str) -> bool:
        """Whether this is the first time today the user hit the cap.

        Used so each user is told about the cap only once per day.

        Args:
            user: The user's name (case-insensitive).

        Returns:
            ``True`` the first time per user per day, then ``False``.
        """
        with self._lock:
            self._roll()
            key = user.lower()
            if key in self._notified:
                return False
            self._notified.add(key)
            return True

    @property
    def spent_today(self) -> float:
        """Today's total spend in USD."""
        with self._lock:
            self._roll()
            return self._spent
