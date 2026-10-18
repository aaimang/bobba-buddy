"""Read and write ``config.toml`` for the settings window, keeping the user's comments.

Values are addressed by ``(section, key)``: ``("", "whitelist")`` for the top level,
``("avatar", "mimic")``, ``("commands.q", "prefix")``. Writes go through
:func:`~bobba_buddy.config.parse_config` first, so a bad value never reaches the file,
and :class:`~bobba_buddy.config.ConfigStore` picks the new file up by its mtime.
"""

from __future__ import annotations

import dataclasses
import os
import tomllib
from pathlib import Path
from typing import Any

import tomlkit
from tomlkit.items import Table

from ..config import COMMAND_DEFAULTS, COMMAND_NAMES, Config, ConfigError, parse_config

FieldId = tuple[str, str]


def config_fields() -> dict[FieldId, Any]:
    """Every setting with its default value, in config order.

    Returns:
        ``{(section, key): default}``; list settings default to lists.
    """
    base = Config()
    fields: dict[FieldId, Any] = {("", "whitelist"): []}
    for section in (
        "claude",
        "limits",
        "chat",
        "memory",
        "pricing",
        "room",
        "avatar",
        "updates",
        "notify",
    ):
        values = getattr(base, section)
        for f in dataclasses.fields(values):
            fields[(section, f.name)] = _plain(getattr(values, f.name))
    for name in COMMAND_NAMES:
        values = COMMAND_DEFAULTS[name]
        for f in dataclasses.fields(values):
            fields[(f"commands.{name}", f.name)] = _plain(getattr(values, f.name))
    return fields


def _plain(value: Any) -> Any:
    """Tuples and frozensets as lists, so they compare with TOML arrays."""
    return list(value) if isinstance(value, tuple | frozenset) else value


def _source_text(path: Path, example_path: Path | None) -> str:
    """The config text to edit: the file, else the example, else empty."""
    if path.exists():
        return path.read_text(encoding="utf-8")
    if example_path is not None and example_path.exists():
        return example_path.read_text(encoding="utf-8")
    return ""


def _lookup(data: dict[str, Any], section: str) -> dict[str, Any] | None:
    """The table for a dotted section name, or ``None`` if it isn't there."""
    table: Any = data
    for part in section.split(".") if section else ():
        if not isinstance(table, dict) or part not in table:
            return None
        table = table[part]
    return table if isinstance(table, dict) else None


def load_values(path: Path, example_path: Path | None = None) -> dict[FieldId, Any]:
    """The current value of every setting, as the user wrote it.

    Names keep their case (``parse_config`` lowercases them), and missing keys take
    their defaults. A missing file reads as the example.

    Args:
        path: ``config.toml``.
        example_path: ``config.example.toml``, used when ``path`` doesn't exist.

    Returns:
        ``{(section, key): value}`` for every key in :func:`config_fields`.

    Raises:
        ConfigError: If the file isn't valid TOML.
    """
    try:
        data = tomllib.loads(_source_text(path, example_path))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path.name}: {exc}") from exc
    values = config_fields()
    for section, key in values:
        table = _lookup(data, section)
        if table is not None and key in table:
            values[(section, key)] = table[key]
    return values


def _table(doc: tomlkit.TOMLDocument, section: str) -> Any:
    """The tomlkit table for a dotted section, created if missing."""
    table: Any = doc
    for part in section.split(".") if section else ():
        if part not in table:
            table.add(part, tomlkit.table(is_super_table=part == "commands"))
        table = table[part]
        if not isinstance(table, Table):
            raise ConfigError(f"[{section}] must be a table")
    return table


def _toml_value(value: Any, old: Any) -> Any:
    """Convert a window value for writing: whole floats stay ints if they were ints."""
    if isinstance(value, float) and value.is_integer() and isinstance(old, int | None):
        if not isinstance(old, bool):
            return int(value)
    if isinstance(value, tuple | list):
        return [str(v) for v in value]
    return value


def render(
    path: Path, example_path: Path | None, changes: dict[FieldId, Any]
) -> tuple[str, Config]:
    """Apply changes to the config text without writing it.

    Args:
        path: ``config.toml``; when it doesn't exist, the example is the starting point.
        example_path: ``config.example.toml``.
        changes: ``{(section, key): new value}``.

    Returns:
        The new file text and the validated config it produces.

    Raises:
        ConfigError: If the result isn't valid; the message names the bad setting.
    """
    try:
        doc = tomlkit.parse(_source_text(path, example_path))
    except Exception as exc:  # tomlkit raises its own ParseError family
        raise ConfigError(f"{path.name}: {exc}") from exc
    for (section, key), value in changes.items():
        table = _table(doc, section)
        old = table.get(key)
        table[key] = _toml_value(value, old.unwrap() if hasattr(old, "unwrap") else old)
    text = tomlkit.dumps(doc)
    try:
        config = parse_config(tomllib.loads(text))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(str(exc)) from exc
    return text, config


def save_values(path: Path, example_path: Path | None, changes: dict[FieldId, Any]) -> Config:
    """Validate the changes, then write ``config.toml`` atomically.

    Only the changed keys are touched; comments and everything else stay as they are.

    Args:
        path: ``config.toml`` (created from the example if missing).
        example_path: ``config.example.toml``.
        changes: ``{(section, key): new value}``.

    Returns:
        The validated config that was written.

    Raises:
        ConfigError: If the result isn't valid; the file is left untouched.
        OSError: If the file can't be written.
    """
    text, config = render(path, example_path, changes)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return config
