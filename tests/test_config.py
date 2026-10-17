"""Config parsing, validation and hot reload."""

import os

import pytest

from bobba_buddy.config import ConfigError, ConfigStore, load_config, parse_config

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "config.example.toml")


def test_example_config_is_valid():
    config = load_config(__import__("pathlib").Path(EXAMPLE))
    assert config.claude.model == "claude-haiku-5-5"
    assert config.commands["roast"].target_cooldown_s == 60
    assert config.chat.bubble_limit == 100


def test_defaults():
    config = parse_config({})
    assert config.whitelist == frozenset()
    assert config.commands["q"].prefix == "/q"
    assert config.commands["q"].web_search is True
    assert config.commands["joke"].max_bubbles == 2


def test_whitelist_and_opt_out_are_lowercased():
    config = parse_config(
        {
            "whitelist": ["Ali", " BOB "],
            "commands": {"roast": {"opt_out": ["Shy.Girl"]}},
        }
    )
    assert config.whitelist == {"ali", "bob"}
    assert config.commands["roast"].opt_out == ("shy.girl",)


@pytest.mark.parametrize(
    "data",
    [
        {"wat": 1},
        {"claude": {"modle": "x"}},
        {"claude": {"effort": "ultra"}},
        {"claude": {"max_tokens": "big"}},
        {"chat": {"bubble_limit": 150}},
        {"limits": {"workers": True}},
        {"whitelist": "Ali"},
        {"commands": {"dance": {}}},
        {"commands": {"joke": {"prefix": "/q"}}},
        {"commands": {"joke": {"prefix": "two words"}}},
        {"commands": {"q": {"max_bubbles": 9}}},
    ],
)
def test_invalid_config_is_rejected(data):
    with pytest.raises(ConfigError):
        parse_config(data)


def test_hot_reload(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('whitelist = ["Ali"]\n')
    now = [0.0]
    reloads, errors = [], []
    store = ConfigStore(
        path,
        check_interval_s=1.0,
        clock=lambda: now[0],
        on_reload=reloads.append,
        on_error=errors.append,
    )
    assert store.get().whitelist == {"ali"}

    path.write_text('whitelist = ["Ali", "Bob"]\n')
    os.utime(path, (1, 1))
    assert store.get().whitelist == {"ali"}  # not checked yet
    now[0] = 2.0
    assert store.get().whitelist == {"ali", "bob"}
    assert len(reloads) == 1

    path.write_text("whitelist = [broken\n")
    os.utime(path, (2, 2))
    now[0] = 4.0
    assert store.get().whitelist == {"ali", "bob"}  # bad edit keeps the old config
    assert len(errors) == 1


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")
