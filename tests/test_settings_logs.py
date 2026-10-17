"""The Extension Logs page: the in-memory log buffer and the terminal view."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pytest

from bobba_buddy.settings import logbuffer
from bobba_buddy.settings.logbuffer import LogBuffer, LogLine
from bobba_buddy.settings.logview import format_line, level_label


@pytest.fixture
def buffer():
    """A buffer attached to a private logger, so tests don't touch the root logger."""
    buf = LogBuffer(maxlen=50, log_file=None)
    logger = logging.getLogger("bobba_buddy.test_logs")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(buf)
    yield buf
    logger.removeHandler(buf)


def log(level: int, message: str, *args) -> None:
    logging.getLogger("bobba_buddy.test_logs").log(level, message, *args)


class TestBuffer:
    def test_keeps_lines_in_order_with_details(self, buffer):
        log(logging.INFO, "hello %s", "Friend1")
        log(logging.WARNING, "careful")
        lines = buffer.since(0)
        assert [line.message for line in lines] == ["hello Friend1", "careful"]
        assert lines[0].source == "test_logs" and lines[0].levelname == "INFO"
        assert lines[0].thread == threading.current_thread().name
        assert lines[1].seq > lines[0].seq == 1

    def test_since_returns_only_newer_lines(self, buffer):
        log(logging.INFO, "one")
        seen = buffer.last_seq
        log(logging.INFO, "two")
        assert [line.message for line in buffer.since(seen)] == ["two"]

    def test_keeps_only_the_latest(self, buffer):
        for i in range(60):
            log(logging.INFO, "line %d", i)
        lines = buffer.since(0)
        assert len(buffer) == 50 and lines[0].message == "line 10"

    def test_tracebacks_are_kept(self, buffer):
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("bobba_buddy.test_logs").exception("failed")
        message = buffer.since(0)[-1].message
        assert message.startswith("failed\nTraceback") and "ValueError: boom" in message

    def test_many_threads(self, buffer):
        def work(n):
            for i in range(20):
                log(logging.INFO, "t%d-%d", n, i)

        threads = [threading.Thread(target=work, args=(n,)) for n in range(5)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        seqs = [line.seq for line in buffer.since(0)]
        assert len(seqs) == 50 and seqs == sorted(seqs) and len(set(seqs)) == 50

    def test_debug_switch(self, buffer):
        root = logging.getLogger()
        before = root.level
        buffer.base_level = logging.INFO
        try:
            buffer.set_debug(True)
            assert buffer.debug and root.level == logging.DEBUG
            buffer.set_debug(False)
            assert not buffer.debug and root.level == logging.INFO
        finally:
            root.setLevel(before)

    def test_install_once(self, tmp_path):
        logbuffer.uninstall()
        try:
            first = logbuffer.install(tmp_path / "a.log")
            assert logbuffer.install(tmp_path / "b.log") is first
            assert first.log_file == tmp_path / "b.log" and logbuffer.current() is first
            assert first in logging.getLogger().handlers
        finally:
            logbuffer.uninstall()
        assert logbuffer.current() is None


class TestFormatting:
    @pytest.mark.parametrize(
        ("level", "label"),
        [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARN"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "ERROR"),
        ],
    )
    def test_level_labels(self, level, label):
        assert level_label(level) == label

    def test_line_parts(self):
        line = LogLine(1, 0.25, logging.WARNING, "WARNING", "buddy-worker_0", "bot", "hi")
        parts = format_line(line)
        assert [tag for _, tag in parts] == ["time", "WARN", "thread", "source", ""]
        assert parts[0][0].endswith(".250 ") and parts[-1][0] == "hi\n"


# --- the page itself (needs a display) ------------------------------------------------

tk = pytest.importorskip("tkinter")


@pytest.fixture
def view(buffer, tmp_path):
    from bobba_buddy.settings.logview import LogView
    from bobba_buddy.settings.theme import Theme

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    opened: list[Path] = []
    buffer.log_file = tmp_path / "bobba-buddy.log"
    page = LogView(root, Theme(root), buffer, opener=opened.append)
    page.pack(fill="both", expand=True)
    page.opened = opened
    yield page
    root.destroy()


def pump(view):
    view._poll()
    view.update_idletasks()


def text(view):
    return view.text.get("1.0", "end-1c")


def test_shows_new_lines_and_follows(view):
    log(logging.INFO, "first")
    log(logging.ERROR, "second")
    pump(view)
    assert "first" in text(view) and "second" in text(view)
    assert view.shown == 2 and view.autoscroll
    assert "Following latest" in view._status.cget("text")


def test_autoscroll_off_counts_new_lines(view):
    view.set_autoscroll(False)
    log(logging.INFO, "while paused")
    pump(view)
    assert view.unseen == 1 and "Paused · 1 new" in view._status.cget("text")
    view.set_autoscroll(True)
    assert view.unseen == 0 and view._autoscroll_pill.get()


def test_scrolling_up_pauses_and_bottom_resumes(view):
    for i in range(200):
        log(logging.INFO, "line %d", i)
    pump(view)
    view.update()
    view.text.yview_moveto(0)
    view._user_scrolled()
    assert not view.autoscroll
    view.text.yview_moveto(1)
    view._user_scrolled()
    assert view.autoscroll


def test_level_chips_and_text_filter(view):
    log(logging.DEBUG, "noisy detail")
    log(logging.INFO, "q from Friend1")
    log(logging.WARNING, "Users entry skipped")
    pump(view)
    view.toggle_level("DEBUG")
    assert "noisy detail" not in text(view) and "q from Friend1" in text(view)
    view.query.set("users")
    view.refilter()
    assert text(view).strip().endswith("Users entry skipped") and view.shown == 1


def test_clear_empties_the_view_only(view, buffer):
    log(logging.INFO, "old")
    pump(view)
    view.clear()
    assert text(view) == "" and len(buffer) == 1
    log(logging.INFO, "new")
    pump(view)
    assert "new" in text(view) and "old" not in text(view)


def test_copy_puts_lines_on_the_clipboard(view):
    log(logging.INFO, "copy me")
    pump(view)
    view.copy()
    assert "copy me" in view.clipboard_get()


def test_open_log_file(view):
    view.open_log_file()
    assert view.opened == [] and "no log file yet" in view._count.cget("text")
    view._buffer.log_file.write_text("x")
    view.open_log_file()
    assert view.opened == [view._buffer.log_file]


def test_debug_button(view, buffer):
    root = logging.getLogger()
    before = root.level
    buffer.base_level = logging.INFO
    try:
        view.toggle_debug()
        assert buffer.debug
        view.toggle_debug()
        assert not buffer.debug
    finally:
        root.setLevel(before)
