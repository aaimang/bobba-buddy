"""Reading and writing config.toml for the settings window."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bobba_buddy.config import ConfigError, load_config
from bobba_buddy.settings.configfile import config_fields, load_values, render, save_values

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"

USER_CONFIG = """\
# My settings
whitelist = ["Ali", "BoB"]   # friends

[commands.q]
cooldown_s = 15  # seconds
"""


@pytest.fixture
def config(tmp_path):
    """A small hand-written config.toml with comments."""
    path = tmp_path / "config.toml"
    path.write_text(USER_CONFIG, encoding="utf-8")
    return path


def test_every_config_key_has_a_default():
    fields = config_fields()
    assert fields[("", "whitelist")] == []
    assert fields[("commands.roast", "prefix")] == "/roast"
    assert fields[("avatar", "wake_actions")] == ["wave", "walk", "look"]
    assert fields[("commands.joke", "max_bubbles")] == 2


def test_load_keeps_case_and_fills_defaults(config):
    values = load_values(config, EXAMPLE)
    assert values[("", "whitelist")] == ["Ali", "BoB"]  # parse_config would lowercase
    assert values[("commands.q", "cooldown_s")] == 15
    assert values[("claude", "effort")] == "low"  # default


def test_missing_file_reads_the_example(tmp_path):
    values = load_values(tmp_path / "config.toml", EXAMPLE)
    assert values[("", "whitelist")] == ["Friend1", "Friend2"]


def test_save_keeps_comments_and_untouched_keys(config):
    save_values(
        config,
        EXAMPLE,
        {("commands.q", "cooldown_s"): 30.0, ("avatar", "autopilot"): False},
    )
    text = config.read_text(encoding="utf-8")
    assert "# My settings" in text
    assert 'whitelist = ["Ali", "BoB"]   # friends' in text
    assert "cooldown_s = 30  # seconds" in text  # whole float written as int, comment kept
    assert "[avatar]" in text and "autopilot = false" in text
    assert load_config(config).avatar.autopilot is False


def test_save_writes_lists_and_floats(config):
    config_out = save_values(
        config,
        EXAMPLE,
        {
            ("", "whitelist"): ["Ali", "Cara"],
            ("avatar", "wake_actions"): ["walk"],
            ("limits", "daily_budget_usd"): 2.5,
            ("commands.roast", "opt_out"): ["Shy"],
        },
    )
    data = tomllib.loads(config.read_text(encoding="utf-8"))
    assert data["whitelist"] == ["Ali", "Cara"]
    assert data["commands"]["roast"]["opt_out"] == ["Shy"]
    assert data["limits"]["daily_budget_usd"] == 2.5
    assert config_out.avatar.wake_actions == ("walk",)
    assert config_out.whitelist == {"ali", "cara"}


def test_invalid_change_leaves_the_file_alone(config):
    before = config.read_bytes()
    with pytest.raises(ConfigError, match=r"\[commands.roast\] prefix '/q'"):
        save_values(config, EXAMPLE, {("commands.roast", "prefix"): "/q"})
    with pytest.raises(ConfigError, match=r"\[avatar\] autopilot_min_s"):
        save_values(config, EXAMPLE, {("avatar", "autopilot_max_s"): 6.0})
    assert config.read_bytes() == before
    assert not (config.parent / "config.toml.tmp").exists()


def test_missing_file_is_created_from_the_example(tmp_path):
    path = tmp_path / "config.toml"
    save_values(path, EXAMPLE, {("", "whitelist"): ["Me"]})
    text = path.read_text(encoding="utf-8")
    assert "# Copy to config.toml." in text  # the example's comments came along
    assert load_config(path).whitelist == {"me"}


def test_render_does_not_write(config):
    before = config.read_bytes()
    text, parsed = render(config, EXAMPLE, {("claude", "effort"): "high"})
    assert 'effort = "high"' in text and parsed.claude.effort == "high"
    assert config.read_bytes() == before


def test_unreadable_toml_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("whitelist = [broken\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_values(path, EXAMPLE)
    with pytest.raises(ConfigError):
        save_values(path, EXAMPLE, {("claude", "effort"): "high"})
