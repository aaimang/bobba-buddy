"""Live check against the Claude API: one call per command (~$0.02 total).

Confirms the key and model work, that web search is accepted on the configured model,
and that replies respect each command's length cap.

Usage::

    uv run python scripts/smoke_claude.py

Attributes:
    PROJECT (Path): The project folder (parent of ``scripts/``).
    CASES (list[tuple]): ``(command, argument, roast target, target motto)`` for each
        live call.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

import anthropic  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from bobba_buddy.config import ConfigError, load_config, parse_config  # noqa: E402
from bobba_buddy.llm import ClaudeResponder, ReplyKind  # noqa: E402
from bobba_buddy.prompts import build_user_message, system_prompt  # noqa: E402
from bobba_buddy.textfmt import sanitize, split_bubbles  # noqa: E402

CASES = [
    ("q", "berapa suhu di Kuala Lumpur hari ini?", None, None),
    ("roast", "", "Bob.Builder", "rares collector since 2009"),
    ("suggest", "room theme for a cozy cafe", None, None),
    ("joke", "ducks", None, None),
]


def main() -> int:
    """Run one live call per command and print each reply, its length and cost.

    Returns:
        0 if every reply succeeded within its length limit, otherwise 1.
    """
    load_dotenv(PROJECT / ".env")
    try:
        config = load_config(PROJECT / "config.toml")
    except ConfigError:
        config = parse_config({})
        print("(no config.toml, using defaults)")

    responder = ClaudeResponder(anthropic.Anthropic(), pricing=lambda: config.pricing)
    total = 0.0
    failures = 0
    for command, arg, target, motto in CASES:
        cmd_cfg = config.commands[command]
        message = build_user_message(
            command,
            asker="SmokeTest",
            arg=arg,
            today=date.today(),
            target_name=target,
            target_motto=motto,
        )
        reply = responder.respond(
            system=system_prompt(command, cmd_cfg.max_chars),
            messages=[{"role": "user", "content": message}],
            web_search=cmd_cfg.web_search,
            claude=config.claude,
        )
        total += reply.cost_usd
        text = sanitize(reply.text)
        bubbles = split_bubbles(text, config.chat.bubble_limit, cmd_cfg.max_bubbles) if text else []
        over = len(text) > cmd_cfg.max_chars
        ok = reply.kind is ReplyKind.OK and not over
        failures += not ok
        print(f"\n/{command} {arg or target or ''}".rstrip())
        print(
            f"  result={reply.kind.value} stop={reply.stop_reason} searches={reply.searches} "
            f"tokens={reply.input_tokens}/{reply.output_tokens} cost=${reply.cost_usd:.5f}"
        )
        if reply.error:
            print(f"  error: {reply.error}")
        print(f"  {len(text)}/{cmd_cfg.max_chars} chars{'  (OVER LIMIT)' if over else ''}")
        for bubble in bubbles:
            print(f"  [{len(bubble):3d}] {bubble}")
        if command == "q" and cmd_cfg.web_search and reply.searches == 0:
            print("  note: no web search happened; check it's enabled for your org in Console")

    print(f"\ntotal cost ${total:.4f}; {'all OK' if not failures else f'{failures} problem(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
