"""Chat command registry and parsing.

Attributes:
    SPECS (dict[str, CommandSpec]): The fixed properties of each command, keyed by its
        internal name (``q``, ``roast``, ``suggest``, ``joke``). Prefixes and limits
        come from config instead (:class:`~bobba_buddy.config.CommandConfig`).
    ParsedKind: What a matched command asks for: ``"run"``, ``"usage"`` or ``"reset"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from .config import COMMAND_NAMES, CommandConfig


@dataclass(frozen=True)
class CommandSpec:
    """Fixed, code-level properties of a chat command.

    Attributes:
        name: Internal command name, also its ``[commands.<name>]`` config section.
        usage: Argument hint shown in help, e.g. ``"<question>"`` or ``"[name]"``.
        arg_required: Whether a bare command shows usage instead of running.
        uses_memory: Whether the asker's recent history is sent with the request.
        arg_is_target: Whether the argument names a user in the room (``/roast``).
    """

    name: str
    usage: str  # argument hint shown in help, e.g. "<question>"
    arg_required: bool
    uses_memory: bool = False
    arg_is_target: bool = False  # the argument names a user in the room


SPECS: dict[str, CommandSpec] = {
    "q": CommandSpec("q", "<question>", arg_required=True, uses_memory=True),
    "roast": CommandSpec("roast", "[name]", arg_required=False, arg_is_target=True),
    "suggest": CommandSpec("suggest", "<topic>", arg_required=True),
    "joke": CommandSpec("joke", "[topic]", arg_required=False),
}
assert tuple(SPECS) == COMMAND_NAMES

ParsedKind = Literal["run", "usage", "reset"]


@dataclass(frozen=True)
class ParsedCommand:
    """A chat line recognised as a command.

    Attributes:
        kind: ``"run"`` to execute, ``"usage"`` to show help, ``"reset"`` to clear
            ``/q`` memory.
        name: Internal command name (a key of ``SPECS``).
        arg: Text after the prefix, stripped; empty if none.
    """

    kind: ParsedKind
    name: str
    arg: str = ""


def parse_command(text: str, commands: Mapping[str, CommandConfig]) -> ParsedCommand | None:
    """Match text against the enabled command prefixes.

    Matching is case-insensitive and needs a space or the end of the text after the
    prefix, so ``/qme`` is not ``/q``. A bare ``/q`` (or a bare command whose
    argument is required) becomes a usage request, and ``/q reset`` becomes a reset.

    Args:
        text: A chat line.
        commands: Per-command config, keyed by internal name.

    Returns:
        The parsed command, or ``None`` for ordinary chat.
    """
    stripped = text.strip()
    lowered = stripped.lower()
    for name, cfg in commands.items():
        if not cfg.enabled:
            continue
        prefix = cfg.prefix.strip().lower()
        if lowered != prefix and not lowered.startswith(prefix + " "):
            continue
        arg = stripped[len(prefix) :].strip()
        if name == "q" and arg.lower() == "reset":
            return ParsedCommand("reset", name)
        if not arg and (name == "q" or SPECS[name].arg_required):
            return ParsedCommand("usage", name)
        return ParsedCommand("run", name, arg)
    return None


def usage_text(name: str, commands: Mapping[str, CommandConfig]) -> str:
    """Help text for a bare command.

    Args:
        name: Internal command name.
        commands: Per-command config, keyed by internal name.

    Returns:
        For ``q``, a list of every enabled command and ``reset``; for any other
        command, its own usage line.
    """
    if name == "q":
        parts = [f"{commands[n].prefix} {SPECS[n].usage}" for n in SPECS if commands[n].enabled]
        parts.append(f"{commands['q'].prefix} reset")
        return "Try: " + " | ".join(parts)
    return f"Usage: {commands[name].prefix} {SPECS[name].usage}"
