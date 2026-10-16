"""Show what the bot has spent on Claude. Read-only; safe to run while the bot is running.

Two sources:

* ``state/budget.json``: today's running total, the same number the daily cap checks.
* ``logs/askbot.log*``: one line per Claude call (cost, tokens, searches, who asked).

Usage::

    uv run python scripts/cost_report.py              # today + last 7 days
    uv run python scripts/cost_report.py --days 30
    uv run python scripts/cost_report.py --date 2026-10-13

Attributes:
    PROJECT (Path): The project folder (parent of ``scripts/``).
    LINE (re.Pattern): Matches the bot's per-call ``cmd=…`` log lines.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
LINE = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2}) \S+ .*?cmd=(?P<cmd>\S+) asker=(?P<asker>\S+) .*?"
    r"result=(?P<result>\S+) .*?in=(?P<inp>\d+) out=(?P<out>\d+) searches=(?P<searches>\d+) "
    r"cost=\$(?P<cost>[\d.]+)"
)


def read_calls(log_dir: Path) -> list[dict]:
    """Collect every logged Claude call from the current and rotated log files.

    Args:
        log_dir: The ``logs/`` folder.

    Returns:
        One dict per call with ``day``, ``cmd``, ``asker``, ``result``, ``in``,
        ``out``, ``searches`` and ``cost``.
    """
    calls = []
    for path in sorted(log_dir.glob("askbot.log*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LINE.match(line)
            if m:
                d = m.groupdict()
                calls.append(
                    {
                        "day": d["day"],
                        "cmd": d["cmd"],
                        "asker": d["asker"],
                        "result": d["result"],
                        "in": int(d["inp"]),
                        "out": int(d["out"]),
                        "searches": int(d["searches"]),
                        "cost": float(d["cost"]),
                    }
                )
    return calls


def daily_cap() -> float | None:
    """Read ``[limits] daily_budget_usd`` from ``config.toml``.

    Returns:
        The cap (1.0 if not set), or ``None`` if the config can't be read.
    """
    try:
        with (PROJECT / "config.toml").open("rb") as fh:
            return float(tomllib.load(fh).get("limits", {}).get("daily_budget_usd", 1.0))
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return None


def table(title: str, rows: dict[str, list[dict]]) -> None:
    """Print a cost table, most expensive row first.

    Args:
        title: Heading printed above the table.
        rows: Calls grouped by row label (command or person).
    """
    print(f"\n{title}")
    print(f"  {'':<16}{'calls':>6}{'searches':>10}{'tokens in/out':>18}{'cost':>11}")
    for key, items in sorted(rows.items(), key=lambda kv: -sum(c["cost"] for c in kv[1])):
        tokens = f"{sum(c['in'] for c in items):,}/{sum(c['out'] for c in items):,}"
        cost = sum(c["cost"] for c in items)
        searches = sum(c["searches"] for c in items)
        print(f"  {key:<16}{len(items):>6}{searches:>10}{tokens:>18}{f'${cost:.4f}':>11}")


def main() -> int:
    """Print today's total against the cap, recent days, and one day's breakdown.

    Returns:
        Always 0.
    """
    parser = argparse.ArgumentParser(description="Claude spend report for the Habbo bot")
    parser.add_argument("--days", type=int, default=7, help="history length (default 7)")
    parser.add_argument("--date", help="detail for this day (YYYY-MM-DD, default today)")
    args = parser.parse_args()

    today = date.today().isoformat()
    day = args.date or today
    cap = daily_cap()

    # Today's total as the bot's budget check sees it.
    print("Today (state/budget.json — what the daily cap uses)")
    try:
        state = json.loads((PROJECT / "state" / "budget.json").read_text())
    except (OSError, ValueError):
        state = {}
    if state.get("date") == today:
        spent = float(state.get("spent_usd", 0))
        cap_txt = (
            "no cap"
            if not cap
            else f"cap ${cap:.2f}, {spent / cap:.1%} used, ${cap - spent:.4f} left"
        )
        print(f"  ${spent:.4f} over {state.get('requests', 0)} requests  ({cap_txt})")
    else:
        print("  $0.0000 — nothing spent yet today")

    calls = read_calls(PROJECT / "logs")
    if not calls:
        print("\nNo Claude calls found in logs/askbot.log*.")
        return 0

    # History by day.
    start = (datetime.fromisoformat(today) - timedelta(days=args.days - 1)).date().isoformat()
    by_day: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        if c["day"] >= start:
            by_day[c["day"]].append(c)
    print(f"\nLast {args.days} days (from logs)")
    for d in sorted(by_day):
        cost = sum(c["cost"] for c in by_day[d])
        print(f"  {d}  {len(by_day[d]):>4} calls  ${cost:.4f}")
    total = sum(c["cost"] for c in calls)
    print(f"  all logged history: {len(calls)} calls, ${total:.4f}")

    # Detail for one day.
    selected = [c for c in calls if c["day"] == day]
    if selected:
        by_cmd: dict[str, list[dict]] = defaultdict(list)
        by_asker: dict[str, list[dict]] = defaultdict(list)
        for c in selected:
            by_cmd["/" + c["cmd"]].append(c)
            by_asker[c["asker"]].append(c)
        table(f"{day} by command", by_cmd)
        table(f"{day} by person", by_asker)
        failed = sum(1 for c in selected if c["result"] != "ok")
        if failed:
            print(f"\n  {failed} call(s) didn't return an answer (busy/refusal/error)")
    else:
        print(f"\nNo calls logged on {day}.")
    print(
        "\nNote: log files rotate (about 6 MB kept), so very old history drops off;"
        " the daily cap always uses state/budget.json."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
