"""SettingsHost: opening the window on the main thread, safely."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from bobba_buddy.settings.host import BotBridge, SettingsHost


class FakeBridge:
    """Records console notices."""

    def __init__(self):
        """No notices yet."""
        self.notices: list[tuple[str, str]] = []

    def status(self):
        return {}

    def set_enabled(self, on):
        pass

    def reload(self):
        pass

    def notice(self, text, color="black"):
        self.notices.append((text, color))


class TclError(Exception):
    """Same name as tkinter's, which the host recognises without importing tkinter."""


def make_host(runner):
    host = SettingsHost(Path("config.toml"), None, runner=lambda: runner)
    host.bridge = FakeBridge()
    return host


def test_request_open_never_blocks():
    host = make_host(lambda *a: None)
    started = time.monotonic()
    for _ in range(100):
        host.request_open()
    assert time.monotonic() - started < 0.5


def test_serve_opens_once_for_many_clicks_and_stops_when_done():
    opened = []
    stop = threading.Event()

    def runner(bridge, config_path, example_path, done, requests):
        opened.append(threading.current_thread().name)
        stop.set()

    host = make_host(runner)
    host.request_open()
    host.request_open()
    host.request_open()
    host.serve(stop.is_set, poll_s=0.01)
    assert opened == [threading.current_thread().name]  # ran on the serving thread


def test_clicks_while_open_go_to_the_window():
    seen = []
    stop = threading.Event()

    def runner(bridge, config_path, example_path, done, requests):
        clicker = threading.Thread(target=host.request_open)
        clicker.start()
        clicker.join()
        seen.append(requests.get(timeout=1))  # the open window raises itself
        stop.set()

    host = make_host(runner)
    host.request_open()
    host.serve(stop.is_set, poll_s=0.01)
    assert seen == [None]


def test_done_ends_serve_without_a_request():
    host = make_host(lambda *a: None)
    calls = []
    host.serve(lambda: calls.append(1) or len(calls) > 2, poll_s=0.01)
    assert len(calls) == 3


def test_missing_tkinter_becomes_a_notice():
    def no_tk():
        raise ImportError("No module named '_tkinter'")

    host = SettingsHost(Path("config.toml"), None, runner=no_tk)
    host.bridge = bridge = FakeBridge()
    host.open(lambda: False)
    assert "python3-tk" in bridge.notices[0][0]


def test_no_display_becomes_a_notice():
    def runner(*args):
        raise TclError("no display name and no $DISPLAY environment variable")

    host = make_host(runner)
    host.open(lambda: False)
    text, color = host.bridge.notices[0]
    assert "Settings window unavailable" in text and "$DISPLAY" in text and color == "red"


def test_crash_becomes_a_notice():
    def runner(*args):
        raise RuntimeError("boom")

    host = make_host(runner)
    host.open(lambda: False)
    assert "boom" in host.bridge.notices[0][0]


def test_no_bridge_no_window():
    opened = []
    host = SettingsHost(Path("config.toml"), None, runner=lambda: lambda *a: opened.append(1))
    host.open(lambda: False)
    assert opened == []


def test_bot_bridge_forwards():
    calls = []

    class Bot:
        def status(self):
            return {"enabled": True}

        def set_enabled(self, on):
            calls.append(("enabled", on))

        def notice(self, text, color):
            calls.append(("notice", text, color))

    class Store:
        def reload_now(self):
            calls.append(("reload",))

    bridge = BotBridge(Bot(), Store())
    assert bridge.status() == {"enabled": True}
    bridge.set_enabled(False)
    bridge.reload()
    bridge.notice("hi", "blue")
    assert calls == [("enabled", False), ("reload",), ("notice", "hi", "blue")]
