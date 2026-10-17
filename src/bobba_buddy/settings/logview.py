"""The settings window's Extension Logs page: the bot's log, live, in a terminal view.

Lines come from the in-memory :class:`~bobba_buddy.settings.logbuffer.LogBuffer`
(the same lines as ``logs/bobba-buddy.log``), polled every 200 ms on the Tk thread.
Auto-scroll follows the newest line; scrolling up turns it off and scrolling back to
the bottom turns it on again.

Attributes:
    TITLE (str): The page's sidebar name and box title.
    POLL_MS (int): How often new lines are fetched.
    LEVELS (tuple[tuple[str, int], ...]): Filter chips: label and lowest level shown.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path

from . import theme as t
from .logbuffer import MAX_LINES, LogBuffer, LogLine
from .theme import Theme
from .widgets import ForumButton, Pill

TITLE = "Extension Logs"
POLL_MS = 200
LEVELS = (
    ("DEBUG", logging.DEBUG),
    ("INFO", logging.INFO),
    ("WARN", logging.WARNING),
    ("ERROR", logging.ERROR),
)

_BG = "#0d1117"
_FG = "#c9d1d9"
_TAGS = {
    "time": "#6e7681",
    "thread": "#d2a8ff",
    "source": "#7ee787",
    "DEBUG": "#8b949e",
    "INFO": "#58a6ff",
    "WARN": "#e3b341",
    "ERROR": "#ff7b72",
}
_CHIP_ON = {
    "DEBUG": ("#eeeeee", "#888888", "#555555"),
    "INFO": ("#effeff", "#2792b9", "#185c75"),
    "WARN": ("#fff4cc", "#c89b00", "#7a5c00"),
    "ERROR": ("#fde8e6", "#d9534f", "#8b1a1a"),
}


def level_label(level: int) -> str:
    """The chip a level belongs to.

    Args:
        level: A ``logging`` level number.

    Returns:
        ``DEBUG``, ``INFO``, ``WARN`` or ``ERROR``.
    """
    if level >= logging.ERROR:
        return "ERROR"
    if level >= logging.WARNING:
        return "WARN"
    if level >= logging.INFO:
        return "INFO"
    return "DEBUG"


def format_line(line: LogLine) -> list[tuple[str, str]]:
    """Split a log line into coloured parts.

    Args:
        line: The log line.

    Returns:
        ``(text, tag)`` pairs; the whole line ends with a newline.
    """
    stamp = time.strftime("%H:%M:%S", time.localtime(line.created))
    millis = int(line.created % 1 * 1000)
    label = level_label(line.level)
    return [
        (f"{stamp}.{millis:03d} ", "time"),
        (f"{label:<5} ", label),
        (f"{line.thread[:14]:<14} ", "thread"),
        (f"{line.source[:8]:<8} ", "source"),
        (line.message + "\n", ""),
    ]


def open_path(path: Path) -> None:
    """Open a file with the system's default app.

    Args:
        path: The file.
    """
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])  # noqa: S603, S607
    elif sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607


class LogView(tk.Frame):
    """The Extension Logs page.

    Attributes:
        autoscroll: Whether the view follows the newest line.
        shown: Lines currently in the terminal.
        unseen: New lines that arrived while auto-scroll was off.
    """

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        buffer: LogBuffer | None,
        *,
        opener: Callable[[Path], None] = open_path,
    ) -> None:
        """Build the page and start polling.

        Args:
            parent: Parent widget.
            th: Theme.
            buffer: Where the log lines come from; ``None`` shows a hint instead.
            opener: Opens the log file; injectable for tests.
        """
        super().__init__(parent, bg=t.BLACK)
        self.th = th
        self._buffer = buffer
        self._opener = opener
        self._seq = 0  # newest line fetched from the buffer
        self._cleared = 0  # lines up to here were cleared from the view
        self._levels = {label: True for label, _ in LEVELS}
        self.autoscroll = True
        self.shown = 0
        self.unseen = 0
        self._refilter: str | None = None

        inner = tk.Frame(self, bg=t.WHITE)
        inner.pack(fill="both", expand=True, padx=th.px(2), pady=(th.px(2), th.px(3)))
        self._build_header(inner)
        self._build_toolbar(inner)
        self._build_status(inner)  # packed bottom-up, so the terminal fills the rest
        self._build_terminal(inner)
        if buffer is None:
            self._write([("Log capture isn't running in this process.\n", "WARN")])
        self.after(POLL_MS, self._poll)

    # --- layout ------------------------------------------------------------------

    def _build_header(self, parent: tk.Misc) -> None:
        """Yellow title bar with the log file's location."""
        th = self.th
        header = tk.Frame(parent, bg=t.YELLOW, padx=th.px(10), pady=th.px(5))
        header.pack(fill="x")
        icon = th.icon("terminal")
        if icon is not None:
            tk.Label(header, image=icon, bg=t.YELLOW).pack(side="left", padx=(0, th.px(6)))
        tk.Label(header, text=TITLE, font=th.font(13, bold=True), bg=t.YELLOW, fg=t.INK).pack(
            side="left"
        )
        log_file = self._buffer.log_file if self._buffer is not None else None
        if log_file is not None:
            where = "/".join(log_file.parts[-2:])
            tk.Label(header, text=where, font=th.font(11), bg=t.YELLOW, fg=t.INK).pack(side="right")
        tk.Frame(parent, bg=t.BLACK, height=th.px(2)).pack(fill="x")

    def _build_toolbar(self, parent: tk.Misc) -> None:
        """Level chips, the text filter and the Auto-scroll switch."""
        th = self.th
        bar = tk.Frame(parent, bg=t.WHITE, padx=th.px(8), pady=th.px(6))
        bar.pack(fill="x")
        icon = th.icon("filter", "tool")
        if icon is not None:
            tk.Label(bar, image=icon, bg=t.WHITE).pack(side="left", padx=(0, th.px(4)))
        self._chips: dict[str, tuple[tk.Frame, tk.Label]] = {}
        for label, _ in LEVELS:
            border = tk.Frame(bar, padx=1, pady=1)
            chip = tk.Label(border, text=label, font=th.font(10, bold=True), padx=th.px(7))
            chip.pack()
            border.pack(side="left", padx=(0, th.px(4)))
            for widget in (border, chip):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", lambda _e, lb=label: self.toggle_level(lb))
            self._chips[label] = (border, chip)
        self._paint_chips()

        search = tk.Frame(bar, bg=t.BLACK, padx=th.px(2), pady=th.px(2))
        search.pack(side="left", padx=(th.px(6), 0))
        box = tk.Frame(search, bg=t.WHITE, padx=th.px(4))
        box.pack()
        icon = th.icon("search", "tool")
        if icon is not None:
            tk.Label(box, image=icon, bg=t.WHITE).pack(side="left")
        self.query = tk.StringVar(self)
        tk.Entry(
            box,
            textvariable=self.query,
            width=16,
            font=th.font(11),
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=t.WHITE,
            fg=t.TEXT,
            insertbackground=t.TEXT,
        ).pack(side="left", padx=(th.px(4), 0), ipady=th.px(1))
        self.query.trace_add("write", lambda *_: self._schedule_refilter())

        self._autoscroll_pill = Pill(bar, th, True, self.set_autoscroll)
        self._autoscroll_pill.pack(side="right")
        tk.Label(
            bar,
            text=" Auto-scroll",
            image=th.icon("arrow-bar-to-down", "tool") or "",
            compound="left",
            font=th.font(11, bold=True),
            bg=t.WHITE,
            fg="#2a3a42",
        ).pack(side="right", padx=(0, th.px(6)))
        tk.Frame(parent, bg=t.LINE, height=1).pack(fill="x")

    def _build_terminal(self, parent: tk.Misc) -> None:
        """The dark, read-only text area with its scrollbars."""
        th = self.th
        term = tk.Frame(parent, bg=_BG)
        term.pack(fill="both", expand=True)
        self.text = tk.Text(
            term,
            bg=_BG,
            fg=_FG,
            font=th.mono(11),
            wrap="none",
            padx=th.px(10),
            pady=th.px(8),
            bd=0,
            highlightthickness=0,
            insertbackground=_FG,
            selectbackground="#264f78",
            state="disabled",
            height=12,
        )
        yscroll = tk.Scrollbar(term, orient="vertical", command=self._scrollbar)
        xscroll = tk.Scrollbar(term, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.text.pack(side="left", fill="both", expand=True)
        for tag, color in _TAGS.items():
            self.text.tag_configure(tag, foreground=color)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>", "<Prior>", "<Next>"):
            self.text.bind(sequence, lambda _e: self.after_idle(self._user_scrolled), add="+")

    def _build_status(self, parent: tk.Misc) -> None:
        """Bottom bar: following/paused state, line count, and the buttons.

        One row when there's room; on a narrow window the status moves above the
        buttons, so neither is cut off.
        """
        th = self.th
        self._bar = bar = tk.Frame(parent, bg="#f3f3ee", padx=th.px(8), pady=th.px(6))
        bar.pack(side="bottom", fill="x")
        tk.Frame(parent, bg=t.BLACK, height=th.px(2)).pack(side="bottom", fill="x")
        self._status_box = tk.Frame(bar, bg="#f3f3ee")
        self._status = tk.Label(self._status_box, font=th.font(11, bold=True), bg="#f3f3ee")
        self._status.pack(side="left")
        self._count = tk.Label(self._status_box, font=th.font(11), bg="#f3f3ee", fg=t.MUTED)
        self._count.pack(side="left", padx=(th.px(6), 0))
        self._buttons = tk.Frame(bar, bg="#f3f3ee")
        buttons = (
            ("Open log file", "file-text", self.open_log_file),
            ("Clear", "trash", self.clear),
            ("Copy", "copy", self.copy),
        )
        for text, icon, command in buttons:
            ForumButton(
                self._buttons, th, text, command, icon=th.icon(icon, "tool"), small=True
            ).pack(side="right", padx=(th.px(6), 0))
        self._debug_button = ForumButton(
            self._buttons, th, "Debug", self.toggle_debug, icon=th.icon("bug", "tool"), small=True
        )
        self._debug_button.pack(side="right")
        bar.columnconfigure(1, weight=1)
        self._one_row: bool | None = None
        bar.bind("<Configure>", lambda _e: self._relayout())
        self._paint_status()
        self._relayout()

    def _relayout(self) -> None:
        """Put status and buttons on one row if they fit, else on two."""
        needed = (
            self._status_box.winfo_reqwidth()
            + self._buttons.winfo_reqwidth()
            + self.th.px(28)  # bar padding and a gap
        )
        width = self._bar.winfo_width()
        one_row = width <= 1 or width >= needed  # not drawn yet: assume one row
        if one_row == self._one_row:
            return
        self._one_row = one_row
        if one_row:
            self._status_box.grid(row=0, column=0, sticky="w")
            self._buttons.grid(row=0, column=1, sticky="e")
        else:
            self._status_box.grid(row=0, column=0, columnspan=2, sticky="w")
            self._buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(self.th.px(6), 0))

    # --- lines -------------------------------------------------------------------

    def _visible(self, line: LogLine) -> bool:
        """Whether a line passes the level chips and the text filter."""
        if not self._levels[level_label(line.level)]:
            return False
        query = self.query.get().strip().lower()
        if not query:
            return True
        haystack = f"{line.levelname} {line.thread} {line.source} {line.message}".lower()
        return query in haystack

    def _write(self, parts: list[tuple[str, str]]) -> None:
        """Append coloured text (the widget is read-only otherwise)."""
        self.text.configure(state="normal")
        for text, tag in parts:
            self.text.insert("end", text, tag or ())
        self.text.configure(state="disabled")

    def _append(self, lines: list[LogLine]) -> None:
        """Add new lines, drop the oldest beyond the limit, follow if auto-scrolling."""
        parts = [part for line in lines if self._visible(line) for part in format_line(line)]
        if not parts:
            return
        added = sum(1 for line in lines if self._visible(line))
        self._write(parts)
        self.shown += added
        excess = self.shown - MAX_LINES
        if excess > 0:
            self.text.configure(state="normal")
            self.text.delete("1.0", f"{excess + 1}.0")
            self.text.configure(state="disabled")
            self.shown = MAX_LINES
        if self.autoscroll:
            self.text.see("end")
        else:
            self.unseen += added
        self._paint_status()

    def _poll(self) -> None:
        """Fetch new lines from the buffer, then check again soon."""
        if self._buffer is not None:
            lines = self._buffer.since(self._seq)
            if lines:
                self._seq = lines[-1].seq
                self._append([line for line in lines if line.seq > self._cleared])
        self.after(POLL_MS, self._poll)

    def refilter(self) -> None:
        """Redraw everything kept after the filters change."""
        self._refilter = None
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self.shown = self.unseen = 0
        if self._buffer is not None:
            lines = [line for line in self._buffer.since(self._cleared) if line.seq <= self._seq]
            self._append(lines)
        self.text.see("end")
        self._paint_status()

    def _schedule_refilter(self) -> None:
        """Redraw shortly after typing stops, not on every key."""
        if self._refilter is not None:
            self.after_cancel(self._refilter)
        self._refilter = self.after(150, self.refilter)

    # --- controls ----------------------------------------------------------------

    def set_autoscroll(self, on: bool) -> None:
        """Follow the newest line (or stop following).

        Args:
            on: ``True`` to follow.
        """
        self.autoscroll = on
        self._autoscroll_pill.set(on)
        if on:
            self.unseen = 0
            self.text.see("end")
        self._paint_status()

    def _scrollbar(self, *args: str) -> None:
        """Scrollbar drag or click: scroll, then check whether we're at the bottom."""
        self.text.yview(*args)
        self._user_scrolled()

    def _user_scrolled(self) -> None:
        """Pause when you scroll up to read; follow again once you're back at the bottom."""
        at_bottom = self.text.yview()[1] >= 0.999
        if at_bottom != self.autoscroll:
            self.set_autoscroll(at_bottom)

    def toggle_level(self, label: str) -> None:
        """Show or hide one level.

        Args:
            label: ``DEBUG``, ``INFO``, ``WARN`` or ``ERROR``.
        """
        self._levels[label] = not self._levels[label]
        self._paint_chips()
        self.refilter()

    def toggle_debug(self) -> None:
        """Switch the bot's DEBUG logging on or off (until it restarts)."""
        if self._buffer is not None:
            self._buffer.set_debug(not self._buffer.debug)
            if self._buffer.debug and not self._levels["DEBUG"]:
                self.toggle_level("DEBUG")
        self._paint_status()

    def copy(self) -> None:
        """Copy the lines shown to the clipboard."""
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end-1c"))
        self._count.configure(text=f"· copied {self.shown} lines")

    def clear(self) -> None:
        """Empty the view (the log file isn't touched)."""
        self._cleared = self._seq
        self.refilter()

    def open_log_file(self) -> None:
        """Open ``logs/bobba-buddy.log`` in the system's default app."""
        path = self._buffer.log_file if self._buffer is not None else None
        if path is None or not path.exists():
            self._count.configure(text="· no log file yet")
            return
        self._opener(path)

    # --- painting ----------------------------------------------------------------

    def _paint_chips(self) -> None:
        """Colour the level chips by whether they're on."""
        for label, (border, chip) in self._chips.items():
            if self._levels[label]:
                bg, edge, fg = _CHIP_ON[label]
            else:
                bg, edge, fg = "#ffffff", "#cccccc", "#aaaaaa"
            border.configure(bg=edge)
            chip.configure(bg=bg, fg=fg)

    def _paint_status(self) -> None:
        """Update the following/paused line, the count and the Debug button."""
        if self.autoscroll:
            self._status.configure(text="● Following latest", fg="#3b7a1e")
        elif self.unseen:
            self._status.configure(text=f"❚❚ Paused · {self.unseen:,} new", fg="#b36b00")
        else:
            self._status.configure(text="❚❚ Paused", fg="#b36b00")
        self._count.configure(text=f"· {self.shown:,} lines")
        debug = self._buffer is not None and self._buffer.debug
        self._debug_button.set_color("yellow" if debug else "grey")
        self.after_idle(self._relayout)  # the status text's width may have changed
