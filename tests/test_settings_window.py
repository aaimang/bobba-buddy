"""The settings window itself (skipped where no display is available, e.g. Linux CI)."""

from __future__ import annotations

import os
import queue
from pathlib import Path

import pytest

tk = pytest.importorskip("tkinter")

from bobba_buddy.config import load_config  # noqa: E402
from bobba_buddy.settings import window as window_mod  # noqa: E402
from bobba_buddy.settings.schema import PAGES  # noqa: E402
from bobba_buddy.settings.window import ABOUT, SettingsWindow, run_window  # noqa: E402

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"


class FakeBridge:
    """The running bot, as the window sees it."""

    def __init__(self):
        """Switched on, no error, nothing reloaded."""
        self.enabled = True
        self.fatal_error = None
        self.reloads = 0
        self.switches: list[bool] = []

    def status(self):
        return {
            "enabled": self.enabled,
            "fatal_error": self.fatal_error,
            "own_name": "Owner",
            "spent_usd": 0.25,
            "budget_usd": 1.0,
        }

    def set_enabled(self, on):
        self.enabled = on
        self.switches.append(on)

    def reload(self):
        self.reloads += 1

    def notice(self, text, color="black"):
        pass


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    r.withdraw()
    yield r
    try:
        r.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    return path


@pytest.fixture
def win(root, config):
    bridge = FakeBridge()
    w = SettingsWindow(root, bridge, config, EXAMPLE)
    w.bridge = bridge
    root.update_idletasks()
    return w


def test_builds_every_page(win):
    for page in [p.name for p in PAGES] + [ABOUT]:
        win.show_page(page)
        assert win.page == page
    assert len(win._controls) > 60


def test_header_shows_status_and_switch(win):
    assert "Connected as Owner" in win._status.cget("text")
    assert "$0.25 / $1.00 today" in win._status.cget("text")
    win._power.toggle()
    assert win.bridge.switches == [False]
    win.bridge.fatal_error = "AuthenticationError"
    win.refresh_status()
    assert "Paused: AuthenticationError" in win._status.cget("text")


def test_edit_and_save(win, config):
    win._controls[("avatar", "autopilot")].toggle()
    win._controls[("commands.q", "cooldown_s")].nudge(1)
    win._controls[("", "whitelist")].entry.insert("end", ", Cara")
    assert len(win.pending) == 3
    assert "3 unsaved changes" in win._msg.cget("text")
    assert win.save()
    saved = load_config(config)
    assert saved.avatar.autopilot is False
    assert saved.commands["q"].cooldown_s == 16
    assert "cara" in saved.whitelist
    assert win.pending == {} and win.bridge.reloads == 1
    assert "Saved and applied" in win._msg.cget("text")
    assert "# Copy to config.toml." in config.read_text(encoding="utf-8")


def test_changing_back_is_not_pending(win):
    pill = win._controls[("avatar", "mimic")]
    pill.toggle()
    pill.toggle()
    assert win.pending == {}


def test_bad_value_points_at_the_field(win, config):
    before = config.read_bytes()
    win.show_page("Whitelist")
    win._controls[("commands.roast", "prefix")].set("/q")
    win._changed(("commands.roast", "prefix"), "/q")
    assert not win.save()
    assert config.read_bytes() == before
    assert win.page == "Commands"
    assert win._labels[("commands.roast", "prefix")].cget("fg") == "#d9534f"
    assert "already used" in win._msg.cget("text")
    win.revert()
    assert win.pending == {}
    assert win._controls[("commands.roast", "prefix")].get() == "/roast"


def test_restart_settings_say_so(win):
    win._controls[("limits", "workers")].nudge(1)
    assert win.save()
    assert "Restart the extension" in win._msg.cget("text")


def test_outside_edit_reloads_when_nothing_is_pending(win, config):
    text = config.read_text(encoding="utf-8").replace("max_turns = 6", "max_turns = 9")
    config.write_text(text, encoding="utf-8")
    os.utime(config, (1, 1))
    win._tick()
    assert win._controls[("memory", "max_turns")].get() == 9
    assert win.saved[("memory", "max_turns")] == 9


def test_close_asks_about_unsaved_changes(win, root, monkeypatch):
    win._controls[("avatar", "mimic")].toggle()
    monkeypatch.setattr(window_mod.messagebox, "askyesnocancel", lambda *a, **k: None)
    win.close()
    assert root.winfo_exists()  # cancelled
    monkeypatch.setattr(window_mod.messagebox, "askyesnocancel", lambda *a, **k: False)
    win.close()
    with pytest.raises(tk.TclError):
        root.winfo_exists()


def test_run_window_closes_when_done(root, config, monkeypatch):
    root.destroy()
    monkeypatch.setattr(window_mod, "activate_app", lambda: None)
    try:
        run_window(FakeBridge(), config, EXAMPLE, lambda: True, queue.Queue())
    except tk.TclError:
        pytest.skip("no display")


def test_window_opens_without_a_logo(root, config, monkeypatch):
    def broken(*args, **kwargs):
        raise tk.TclError("")

    monkeypatch.setattr(window_mod.tk, "PhotoImage", broken)
    w = SettingsWindow(root, FakeBridge(), config, EXAMPLE)
    assert w._logo is None
    w.show_page(ABOUT)


def test_theme_icons(win):
    assert win.th.icon("users", "nav") is not None
    assert win.th.icon("users", "nav") is win.th.icon("users", "nav")  # cached
    assert win.th.icon("no-such-icon") is None
    assert win.th.icon("") is None
