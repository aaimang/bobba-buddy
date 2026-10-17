"""The settings window: a Habbo-style Tk window that edits ``config.toml``.

Built from :data:`~bobba_buddy.settings.schema.PAGES`. Changes are collected until
Save, then validated and written by :func:`~bobba_buddy.settings.configfile.save_values`
(comments kept), and the bot reloads them at once. The ON/OFF switch in the header
talks to the running bot directly.

Attributes:
    LOGO_PATH (Path): The Bobba Buddy logo shipped inside the package.
    ABOUT (str): Name of the About page.
    LOGS (str): Name of the Extension Logs page.
"""

from __future__ import annotations

import logging
import queue
import re
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import messagebox
from typing import Any

from .. import __version__
from . import logbuffer
from . import theme as t
from .configfile import FieldId, config_fields, load_values, save_values
from .host import Bridge, Done
from .logview import TITLE, LogView
from .schema import BOX_ICONS, PAGE_ICONS, PAGES, Field, all_fields
from .theme import (
    Theme,
    activate_app,
    enable_dpi_awareness,
    hide_from_dock,
    load_png,
    name_app,
)
from .widgets import (
    Box,
    Chips,
    Choice,
    ForumButton,
    NamesField,
    Pill,
    ScrollArea,
    Sidebar,
    Stepper,
    TextField,
)

log = logging.getLogger(__name__)

LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logo.png"
ABOUT = "About"
LOGS = TITLE
_ERROR_KEY = re.compile(r"\[([\w.]+)\] (\w+)")


def load_logo(root: tk.Misc) -> tk.PhotoImage | None:
    """The logo as a Tk image, or ``None`` if Tk can't create it.

    See :func:`~bobba_buddy.settings.theme.load_png`.

    Args:
        root: The Tk root.

    Returns:
        The image, or ``None``.
    """
    return load_png(root, LOGO_PATH)


def _same(a: Any, b: Any) -> bool:
    """Whether two setting values are equal (1 and 1.0 are)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(a - b) < 1e-9
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return list(a) == list(b)
    return a == b


class SettingsWindow:
    """The window's contents and state.

    Attributes:
        root: The Tk root.
        values: Every setting's current value in the window.
        saved: The values as last loaded or saved.
        pending: Changed settings not saved yet.
        page: The page being shown.
    """

    def __init__(
        self,
        root: tk.Tk,
        bridge: Bridge,
        config_path: Path,
        example_path: Path | None,
        *,
        page: str | None = None,
    ) -> None:
        """Build the window.

        Args:
            root: The Tk root.
            bridge: The running bot.
            config_path: ``config.toml``.
            example_path: ``config.example.toml``, used if ``config.toml`` is missing.
            page: Page to open on; the first one if ``None``.
        """
        self.root = root
        self._bridge = bridge
        self._path = config_path
        self._example = example_path
        self.th = th = Theme(root)
        self.pending: dict[FieldId, Any] = {}
        self._controls: dict[FieldId, Any] = {}
        self._labels: dict[FieldId, tk.Label] = {}
        self._rows: dict[FieldId, tk.Frame] = {}
        self._field_page: dict[FieldId, str] = {}
        self._fields = {f.id: f for f in all_fields()}
        self._pages: dict[str, tk.Frame] = {}
        self._error_field: FieldId | None = None
        self.page = ""

        load_error = None
        try:
            self.values = load_values(config_path, example_path)
        except Exception as exc:  # keep the window usable to fix things
            load_error = str(exc)
            self.values = config_fields()
        self.saved = dict(self.values)
        self._mtime = self._stat()

        root.title("Bobba Buddy settings")
        root.configure(bg=t.PANEL)
        root.geometry(f"{th.px(820)}x{th.px(620)}")
        root.minsize(th.px(680), th.px(460))
        self._logo = load_logo(root)
        self._logo_small = self._logo
        if self._logo is not None:
            if th.scale < 1.75:
                self._logo_small = self._logo.subsample(2)
            root.iconphoto(True, self._logo)

        self._build_header()
        self._build_footer()
        self._build_body()
        for p in PAGES:
            self._build_page(p.name, p.boxes)
        self._build_about()
        self.show_page(page if page in self._pages or page == LOGS else PAGES[0].name)

        root.bind_all("<MouseWheel>", self._wheel)
        root.bind_all("<Button-4>", self._wheel)
        root.bind_all("<Button-5>", self._wheel)
        root.bind_all(
            "<Command-s>" if root.tk.call("tk", "windowingsystem") == "aqua" else "<Control-s>",
            lambda _e: self.save(),
        )
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_status()
        if load_error:
            self._message(f"Couldn't read config.toml: {load_error}", t.RED)
        root.after(1000, self._tick)

    # --- layout ------------------------------------------------------------------

    def _build_header(self) -> None:
        """Yellow bar: logo, title, status line and the bot's ON/OFF switch."""
        th = self.th
        header = tk.Frame(self.root, bg=t.YELLOW, padx=th.px(10), pady=th.px(4))
        header.pack(fill="x")
        tk.Frame(self.root, bg=t.BLACK, height=th.px(2)).pack(fill="x")
        if self._logo_small is not None:
            tk.Label(header, image=self._logo_small, bg=t.YELLOW).pack(side="left")
        titles = tk.Frame(header, bg=t.YELLOW)
        titles.pack(side="left", padx=th.px(10), fill="x", expand=True)
        tk.Label(
            titles,
            text="Bobba Buddy settings",
            font=th.font(17, bold=True),
            bg=t.YELLOW,
            fg=t.INK,
            anchor="w",
        ).pack(fill="x")
        self._status = tk.Label(
            titles, text="", font=th.font(11), bg=t.YELLOW, fg="#5a4500", anchor="w"
        )
        self._status.pack(fill="x")
        self._power = Pill(
            header,
            th,
            True,
            self._bridge.set_enabled,
            labels=("BOT ON", "OFF"),
            size=13,
        )
        self._power.pack(side="right")

    def _build_body(self) -> None:
        """Sidebar on the left, scrolling page and Save/Revert bar on the right."""
        th = self.th
        body = tk.Frame(self.root, bg=t.PANEL)
        body.pack(fill="both", expand=True)
        names = [p.name for p in PAGES] + [LOGS, ABOUT]
        icons = {name: th.icon(PAGE_ICONS.get(name, ""), "nav") for name in names}
        self._sidebar = Sidebar(body, th, names, self.show_page, icons)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)
        tk.Frame(body, bg=t.BLACK, width=th.px(2)).pack(side="left", fill="y")
        content = tk.Frame(body, bg=t.PANEL)
        content.pack(side="left", fill="both", expand=True)

        self._actions = actions = tk.Frame(content, bg=t.PANEL, padx=th.px(10), pady=th.px(8))
        actions.pack(side="bottom", fill="x")
        ForumButton(actions, th, "Save", self.save, color="yellow").pack(side="right")
        ForumButton(actions, th, "Revert", self.revert).pack(side="right", padx=th.px(8))
        self._msg = tk.Label(
            actions,
            text="",
            font=th.font(12),
            bg=t.PANEL,
            fg=t.MUTED,
            anchor="w",
            justify="left",
        )
        self._msg.pack(side="left", fill="x", expand=True)
        self._msg.bind("<Configure>", lambda e: self._msg.configure(wraplength=e.width))

        self._scroll = ScrollArea(content, t.PANEL)
        self._scroll.pack(fill="both", expand=True, padx=(th.px(10), 0), pady=(th.px(10), 0))
        self._logs = LogView(content, th, logbuffer.current())

    def _build_footer(self) -> None:
        """Bottom strip: version, tagline and the GitHub link."""
        th = self.th
        footer = tk.Frame(self.root, bg=t.SIDEBAR, padx=th.px(10), pady=th.px(5))
        footer.pack(side="bottom", fill="x")
        tk.Frame(self.root, bg=t.BLACK, height=th.px(2)).pack(side="bottom", fill="x")
        tk.Label(
            footer,
            text=f"v{__version__} · a hotel sidekick for G-Earth",
            font=th.font(11),
            bg=t.SIDEBAR,
            fg="#3a3a36",
        ).pack(side="left")
        github = th.icon("brand-github", "link")
        link = tk.Label(
            footer,
            text=" GitHub: aaimang/bobba-buddy ↗" if github else "GitHub: aaimang/bobba-buddy ↗",
            image=github or "",
            compound="left",
            font=th.font(11, bold=True),
            bg=t.SIDEBAR,
            fg=t.BLUE_DARK,
            cursor="hand2",
        )
        link.pack(side="right")
        link.bind("<Button-1>", lambda _e: webbrowser.open(t.GITHUB_URL))

    def _build_page(self, name: str, boxes: tuple) -> None:
        """One page of boxes and fields."""
        th = self.th
        frame = tk.Frame(self._scroll.inner, bg=t.PANEL)
        for spec in boxes:
            box = Box(frame, th, spec.title, spec.note, th.icon(BOX_ICONS.get(spec.title, "")))
            box.pack(fill="x", pady=(0, th.px(10)), padx=(0, th.px(10)))
            for field in spec.fields:
                holder, label, row = box.row(field.label, field.help)
                control = self._make_control(holder, field, self.values[field.id])
                control.pack(side="right")
                self._controls[field.id] = control
                self._labels[field.id] = label
                self._rows[field.id] = row
                self._field_page[field.id] = name
        self._pages[name] = frame

    def _make_control(self, parent: tk.Misc, field: Field, value: Any) -> tk.Frame:
        """The right control for a field."""
        th = self.th

        def changed(new: Any, fid: FieldId = field.id) -> None:
            self._changed(fid, new)

        if field.kind == "toggle":
            return Pill(parent, th, bool(value), changed)
        if field.kind == "number":
            return Stepper(
                parent,
                th,
                value,
                changed,
                minimum=field.min,
                maximum=field.max,
                step=field.step,
                decimals=field.decimals,
                unit=field.unit,
            )
        if field.kind == "names":
            return NamesField(parent, th, value, changed, width=34)
        if field.kind == "choice":
            return Choice(parent, th, value, changed, field.options)
        if field.kind == "chips":
            return Chips(parent, th, value, changed, field.options)
        return TextField(parent, th, value, changed)

    def _build_about(self) -> None:
        """The About page: big logo, version, links and the disclaimer."""
        th = self.th
        frame = tk.Frame(self._scroll.inner, bg=t.PANEL)
        title = "About Bobba Buddy"
        box = Box(frame, th, title, icon=th.icon(BOX_ICONS[title]))
        box.pack(fill="x", padx=(0, th.px(10)))
        inner = tk.Frame(box.body, bg=t.WHITE, padx=th.px(14), pady=th.px(14))
        inner.pack(fill="x")
        if self._logo is not None:
            tk.Label(inner, image=self._logo, bg=t.WHITE).pack(side="left", anchor="n")
        text = tk.Frame(inner, bg=t.WHITE)
        text.pack(side="left", fill="x", expand=True, padx=(th.px(16), 0))

        def line(value: str, size: int, bold: bool = False, fg: str = t.TEXT) -> tk.Label:
            label = tk.Label(
                text,
                text=value,
                font=th.font(size, bold),
                bg=t.WHITE,
                fg=fg,
                anchor="w",
                justify="left",
            )
            label.pack(fill="x")
            return label

        line(f"Bobba Buddy {__version__}", 19, bold=True)
        line("A hotel sidekick for G-Earth", 13, fg="#555555")
        buttons = tk.Frame(text, bg=t.WHITE)
        buttons.pack(fill="x", pady=th.px(12))
        ForumButton(
            buttons,
            th,
            "View on GitHub",
            lambda: webbrowser.open(t.GITHUB_URL),
            color="blue",
            icon=th.icon("brand-github", "white"),
        ).pack(side="left")
        ForumButton(buttons, th, "Read the docs", lambda: webbrowser.open(t.DOCS_URL)).pack(
            side="left", padx=th.px(8)
        )
        disclaimer = line(
            "Unofficial fan project, not affiliated with or endorsed by Sulake Oy. Habbo "
            "and the avatar artwork in the logo belong to Sulake Oy.",
            11,
            fg=t.MUTED,
        )
        disclaimer.bind("<Configure>", lambda e: disclaimer.configure(wraplength=e.width))
        self._pages[ABOUT] = frame

    # --- state -------------------------------------------------------------------

    def show_page(self, name: str) -> None:
        """Switch the content area to a page.

        Args:
            name: The page's sidebar name.
        """
        if name == self.page:
            return
        th = self.th
        if self.page == LOGS:
            self._logs.pack_forget()
            self._scroll.pack(fill="both", expand=True, padx=(th.px(10), 0), pady=(th.px(10), 0))
        elif self.page:
            self._pages[self.page].pack_forget()
        # Save and Revert only where there are settings to change.
        if name in (LOGS, ABOUT):
            self._actions.pack_forget()
        elif not self._actions.winfo_manager():  # hidden by About or Extension Logs
            self._actions.pack(side="bottom", fill="x", before=self._scroll)
        if name == LOGS:
            self._scroll.pack_forget()  # the terminal fills the whole area
            self._logs.pack(fill="both", expand=True, padx=th.px(10), pady=th.px(10))
        else:
            self._pages[name].pack(fill="x")
            self._scroll.to_top()
        self.page = name
        self._sidebar.select(name)

    def _wheel(self, event: tk.Event) -> None:
        """Mouse wheel: scroll the settings page (the log terminal scrolls itself)."""
        if self.page != LOGS:
            self._scroll.wheel(event)

    def _changed(self, fid: FieldId, value: Any) -> None:
        """A control changed: track it as pending unless it's back to the saved value."""
        self.values[fid] = value
        if _same(value, self.saved[fid]):
            self.pending.pop(fid, None)
        else:
            self.pending[fid] = value
        self._clear_error()
        count = len(self.pending)
        if count:
            self._message(f"{count} unsaved change{'s' if count > 1 else ''}", t.MUTED)
        else:
            self._message("", t.MUTED)

    def _message(self, text: str, color: str) -> None:
        """Show a line next to Save and Revert."""
        self._msg.configure(text=text, fg=color)

    def _clear_error(self) -> None:
        """Remove the red highlight from the field a save complained about."""
        if self._error_field is not None:
            self._labels[self._error_field].configure(fg=t.TEXT)
            self._error_field = None

    def _show_error(self, message: str) -> None:
        """Show a save error and point at the setting it names, if any."""
        self._message(message, t.RED)
        match = _ERROR_KEY.search(message)
        fid = (match.group(1), match.group(2)) if match else None
        if message.startswith("whitelist"):
            fid = ("", "whitelist")
        if fid in self._labels:
            self._clear_error()
            self.show_page(self._field_page[fid])
            self._labels[fid].configure(fg=t.RED)
            self._error_field = fid
            self._scroll.show(self._rows[fid])

    def _set_controls(self, values: dict[FieldId, Any]) -> None:
        """Show values in their controls without reporting changes."""
        for fid, value in values.items():
            if fid in self._controls:
                self._controls[fid].set(value)

    def save(self) -> bool:
        """Validate and write the pending changes, then apply them.

        Returns:
            ``True`` if there was nothing to save or the save worked.
        """
        self.root.focus_set()  # commit a number being typed
        self.root.update_idletasks()
        if not self.pending:
            self._message("Nothing to save.", t.MUTED)
            return True
        try:
            save_values(self._path, self._example, dict(self.pending))
        except OSError as exc:
            self._message(f"Couldn't write config.toml: {exc}", t.RED)
            return False
        except Exception as exc:  # ConfigError, named after the bad setting
            self._show_error(str(exc))
            return False
        restart = any(self._fields[fid].restart for fid in self.pending if fid in self._fields)
        self.saved.update(self.pending)
        self.pending.clear()
        self._mtime = self._stat()
        try:
            self._bridge.reload()
        except Exception:
            log.exception("reload after save failed")
        note = " Restart the extension for the Load settings." if restart else ""
        self._message(f"Saved and applied.{note}", "#3b7a1e")
        return True

    def revert(self) -> None:
        """Put back the saved values."""
        if not self.pending:
            self._message("No changes to revert.", t.MUTED)
            return
        reverted = {fid: self.saved[fid] for fid in self.pending}
        self._set_controls(reverted)
        self.values.update(reverted)
        self.pending.clear()
        self._clear_error()
        self._message("Changes reverted.", t.MUTED)

    def close(self) -> None:
        """Close, asking first if there are unsaved changes."""
        if self.pending:
            answer = messagebox.askyesnocancel(
                "Unsaved changes", "Save your changes before closing?", parent=self.root
            )
            if answer is None or (answer and not self.save()):
                return
        destroy(self.root)

    # --- live updates ------------------------------------------------------------

    def _stat(self) -> float | None:
        """``config.toml``'s modification time, or ``None``."""
        try:
            return self._path.stat().st_mtime
        except OSError:
            return None

    def refresh_status(self) -> None:
        """Update the header from the bot: ON/OFF, connection, spend, errors."""
        try:
            status = self._bridge.status()
        except Exception:
            log.debug("status failed", exc_info=True)
            return
        self._power.set(status.get("enabled", True))
        error = status.get("fatal_error")
        if error:
            self._status.configure(text=f"Paused: {error}", fg="#8b1a1a")
            return
        name = status.get("own_name")
        parts = [f"Connected as {name}" if name else "Waiting for the hotel"]
        spent, cap = status.get("spent_usd", 0.0), status.get("budget_usd", 0.0)
        parts.append(f"${spent:.2f} / ${cap:.2f} today" if cap else f"${spent:.2f} today")
        self._status.configure(text=" · ".join(parts), fg="#5a4500")

    def _tick(self) -> None:
        """Every second: refresh the header, and pick up outside edits to the file."""
        self.refresh_status()
        mtime = self._stat()
        if mtime is not None and mtime != self._mtime:
            self._mtime = mtime
            if not self.pending:
                try:
                    values = load_values(self._path, self._example)
                except Exception as exc:
                    self._message(f"config.toml changed but can't be read: {exc}", t.RED)
                else:
                    self.values, self.saved = dict(values), dict(values)
                    self._set_controls(values)
                    self._message("config.toml changed on disk; reloaded.", t.BLUE_DARK)
            else:
                self._message(
                    "config.toml changed on disk. Saving only writes the settings you changed.",
                    "#b36b00",
                )
        self.root.after(1000, self._tick)


def destroy(root: tk.Tk) -> None:
    """Close the window, cancelling its timers first.

    Tcl timers belong to the thread, not the window, so a pending ``after`` would
    otherwise fire in the next window and fail with "invalid command name".

    Args:
        root: The Tk root; already closed is fine.
    """
    try:
        for timer in root.tk.splitlist(root.tk.call("after", "info")):
            # Tcl-level cancel: after_cancel() also deletes the callback's command, which
            # fails for timers other widgets registered (destroy() cleans those up).
            root.tk.call("after", "cancel", timer)
        root.destroy()
    except tk.TclError:
        pass  # already closed


def bring_to_front(root: tk.Tk, float_s: float = 10.0) -> None:
    """Show the window above other apps (G-Earth included) and focus it.

    macOS may refuse to activate a background process right after you click in
    another app, so the window also floats on top until it gets focus (or for
    ``float_s`` seconds), then behaves like a normal window.

    Args:
        root: The Tk root.
        float_s: Longest time to float on top without being focused.
    """
    root.deiconify()
    root.lift()
    activate_app()
    root.attributes("-topmost", True)
    root.focus_force()

    def settle(_event: object = None) -> None:
        root.unbind("<FocusIn>")
        try:
            root.attributes("-topmost", False)
        except tk.TclError:
            pass  # window already closed

    root.bind("<FocusIn>", settle)
    root.after(int(float_s * 1000), settle)


def run_window(
    bridge: Bridge,
    config_path: Path,
    example_path: Path | None,
    done: Done,
    requests: queue.Queue[None],
    page: str | None = None,
) -> None:
    """Show the settings window until it's closed or ``done()``; main thread only.

    Further requests on ``requests`` (another ▶ press) raise the open window.

    Args:
        bridge: The running bot.
        config_path: ``config.toml``.
        example_path: ``config.example.toml``.
        done: Returns ``True`` to close the window (the session ended).
        requests: Open requests arriving while the window is open.
        page: Page to open on.

    Raises:
        tkinter.TclError: If no window can be created (no display).
    """
    enable_dpi_awareness()
    name_app()
    root = tk.Tk(className="BobbaBuddy")
    try:
        SettingsWindow(root, bridge, config_path, example_path, page=page)
        bring_to_front(root)

        def poll() -> None:
            if done():
                destroy(root)
                return
            raised = False
            while True:
                try:
                    requests.get_nowait()
                except queue.Empty:
                    break
                raised = True
            if raised:
                bring_to_front(root)
            root.after(200, poll)

        root.after(200, poll)
        root.mainloop()
    finally:
        destroy(root)
        hide_from_dock()
