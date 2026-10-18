"""The update banner and the About page's Updates box (mockup A).

The banner sits under the yellow header and has four states: an update is
available (blue), updating (yellow), updated (green, asks you to restart G-Earth)
and couldn't update (red, with the manual steps). Everything is read from the
:class:`~bobba_buddy.updates.UpdateChecker` and the running
:class:`~bobba_buddy.updater.Updater` once a second; no network call ever runs on
the Tk thread.

Attributes:
    STATES (dict[str, tuple[str, str, str]]): Banner colours per state:
        background, text and icon name/variant.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from tkinter import messagebox

from .. import updates
from ..updater import MANUAL_STEPS, Updater
from . import theme as t
from .host import Bridge
from .theme import Theme
from .widgets import Box, ForumButton

STATES = {
    "available": ("#e8f6fd", "#0f3f52", "cloud-download:info"),
    "running": ("#fff4cc", "#5a4500", "refresh:warn"),
    "done": ("#e6f4dd", "#27500a", "circle-check:ok"),
    "failed": ("#fde8e6", "#8b1a1a", "alert-triangle:bad"),
}


def released_ago(published_at: str, now: float | None = None) -> str:
    """How long ago a release came out, in words.

    Args:
        published_at: GitHub's ISO 8601 time, e.g. ``"2026-10-17T17:16:20Z"``.
        now: Unix time; ``time.time()`` if ``None``.

    Returns:
        ``"released today"``, ``"released yesterday"``, ``"released 3 days ago"``,
        or ``""`` if the time can't be read.
    """
    try:
        when = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    now_dt = datetime.fromtimestamp(time.time() if now is None else now, UTC)
    days = (now_dt - when).days
    if days <= 0:
        return "released today"
    if days == 1:
        return "released yesterday"
    return f"released {days} days ago"


def checked_text(checked_at: float, now: float | None = None) -> str:
    """When the last check happened, in words.

    Args:
        checked_at: Unix time, or 0 for never.
        now: Unix time; ``time.time()`` if ``None``.

    Returns:
        E.g. ``"Checked today at 09:12"`` or ``"Checked on 17 Oct at 09:12"``.
    """
    if not checked_at:
        return "Not checked yet"
    when = time.localtime(checked_at)
    today = time.localtime(time.time() if now is None else now)
    clock = time.strftime("%H:%M", when)
    if when[:3] == today[:3]:
        return f"Checked today at {clock}"
    return f"Checked on {time.strftime('%d %b', when).lstrip('0')} at {clock}"


class UpdateUI:
    """Owns the banner, the About box and the one running update."""

    def __init__(
        self,
        root: tk.Misc,
        th: Theme,
        bridge: Bridge,
        project_dir: Path,
        show_about: Callable[[], None],
        *,
        checker: Callable[[], updates.UpdateChecker | None] = updates.current,
        confirm: Callable[[str, str], bool] | None = None,
        make_updater: Callable[..., Updater] = Updater,
    ) -> None:
        """Set up; call :meth:`build_banner` and :meth:`build_about`, then :meth:`refresh`.

        Args:
            root: The Tk root.
            th: Theme.
            bridge: The running bot, for the G-Earth console notice after an update.
            project_dir: The checkout the bot runs from.
            show_about: Switches the window to the About page.
            checker: Returns the update checker (``None`` if checks aren't running).
            confirm: Asks before updating; a Yes/No dialog by default.
            make_updater: Builds the :class:`Updater`; injectable for tests.
        """
        self.root = root
        self.th = th
        self._bridge = bridge
        self._project_dir = project_dir
        self._show_about = show_about
        self._checker = checker
        self._confirm = confirm or self._ask
        self._make_updater = make_updater
        self.updater: Updater | None = None
        self._announced_result = False
        self._hidden_result = False
        self.state = ""  # banner state shown, "" when hidden

    # --- building ----------------------------------------------------------------

    def build_banner(self, before: tk.Misc) -> None:
        """Create the banner (hidden until there's something to show).

        Args:
            before: The widget the banner is packed above (the window body).
        """
        th = self.th
        self._before = before
        self.banner = tk.Frame(self.root, bg=t.BLACK)
        self._inner = tk.Frame(self.banner, padx=th.px(10), pady=th.px(6))
        self._inner.pack(fill="x", pady=(0, th.px(2)))
        self._text_box = tk.Frame(self._inner)
        self._icon = tk.Label(self._text_box)
        self._icon.pack(side="left", padx=(0, th.px(8)))
        self._text = tk.Label(self._text_box, font=th.font(12), anchor="w", justify="left")
        self._text.pack(side="left")
        self._buttons = tk.Frame(self._inner)
        self._b_update = ForumButton(
            self._buttons,
            th,
            "Update now",
            self.update_now,
            color="yellow",
            icon=th.icon("download", "tool"),
            small=True,
        )
        self._b_release = ForumButton(
            self._buttons,
            th,
            "View release",
            self.view_release,
            color="blue",
            icon=th.icon("external-link", "white"),
            small=True,
        )
        self._b_howto = ForumButton(
            self._buttons,
            th,
            "How to update",
            self._show_about,
            icon=th.icon("clipboard-copy", "tool"),
            small=True,
        )
        self._b_close = ForumButton(
            self._buttons, th, "", self.close, icon=th.icon("x", "tool"), small=True
        )
        self._inner.columnconfigure(1, weight=1)
        self._one_row: bool | None = None
        self._inner.bind("<Configure>", lambda _e: self._relayout())

    def build_about(self, parent: tk.Misc) -> None:
        """Create the Updates box on the About page.

        Args:
            parent: The About page frame.
        """
        th = self.th
        box = Box(parent, th, "Updates", icon=th.icon("refresh"))
        box.pack(fill="x", padx=(0, th.px(10)), pady=(0, th.px(10)))
        row = tk.Frame(box.body, bg=t.WHITE, padx=th.px(10), pady=th.px(8))
        row.pack(fill="x")
        text = tk.Frame(row, bg=t.WHITE)
        text.pack(side="left", fill="x", expand=True)
        self._about_status = tk.Label(
            text, font=th.font(13, bold=True), bg=t.WHITE, fg=t.TEXT, anchor="w", justify="left"
        )
        self._about_status.pack(fill="x")
        self._about_checked = tk.Label(
            text, font=th.font(11), bg=t.WHITE, fg=t.MUTED, anchor="w", justify="left"
        )
        self._about_checked.pack(fill="x")
        buttons = tk.Frame(row, bg=t.WHITE)
        buttons.pack(side="right")
        self._a_check = ForumButton(
            buttons, th, "Check now", self.check_now, icon=th.icon("refresh", "tool"), small=True
        )
        self._a_check.pack(side="right")
        self._a_update = ForumButton(
            buttons,
            th,
            "Update now",
            self.update_now,
            color="yellow",
            icon=th.icon("download", "tool"),
            small=True,
        )
        self._steps = tk.Label(
            box.body,
            text="\n".join(f"{i}. {step}" for i, step in enumerate(MANUAL_STEPS, 1)),
            font=th.mono(11),
            bg="#0d1117",
            fg="#c9d1d9",
            anchor="w",
            justify="left",
            padx=th.px(10),
            pady=th.px(8),
        )
        self._steps_note = tk.Label(
            box.body,
            text="Or update by hand in the project folder:",
            font=th.font(11),
            bg=t.WHITE,
            fg=t.MUTED,
            anchor="w",
            padx=th.px(10),
        )

    # --- actions -----------------------------------------------------------------

    def _ask(self, title: str, message: str) -> bool:
        """A Yes/No dialog."""
        return bool(messagebox.askyesno(title, message, parent=self.root))

    def update_now(self) -> None:
        """Confirm, then run the update on a background thread."""
        checker = self._checker()
        latest = checker.latest if checker else None
        if latest is None or (self.updater is not None and self.updater.state == "running"):
            return
        message = (
            f"Update Bobba Buddy to {latest.version}?\n\n"
            f"This downloads {latest.tag} with git and runs uv sync in {self._project_dir}. "
            "Your config.toml "
            "and .env aren't touched.\n\nRestart G-Earth afterwards to start the new version."
        )
        if not self._confirm("Update Bobba Buddy", message):
            return
        self.updater = self._make_updater(
            self._project_dir, latest.version, checker.current, tag=latest.tag
        )
        self._announced_result = self._hidden_result = False
        self.updater.start()
        self.refresh()

    def view_release(self) -> None:
        """Open the release page on GitHub."""
        checker = self._checker()
        if checker is not None and checker.latest is not None:
            webbrowser.open(checker.latest.url)

    def check_now(self) -> None:
        """Ask GitHub now, on a short-lived thread."""
        checker = self._checker()
        if checker is None or checker.checking:
            return
        threading.Thread(target=checker.check_now, name="buddy-update-check", daemon=True).start()
        self.root.after(50, self.refresh)

    def close(self) -> None:
        """✕: hide this version's banner, or a finished update's result."""
        if self.updater is not None and self.updater.state in ("done", "failed"):
            self._hidden_result = True
        else:
            checker = self._checker()
            if checker is not None:
                checker.dismiss()
        self.refresh()

    # --- painting ----------------------------------------------------------------

    def _banner_state(self) -> tuple[str, str]:
        """Which banner to show, and its text; ``("", "")`` for none."""
        checker = self._checker()
        updater = self.updater
        if updater is not None and updater.state != "idle":
            if updater.state == "running":
                target = updater.target
                return "running", f"Updating to {target}… {updater.step}"
            if not self._hidden_result:
                return updater.state, updater.message
        if checker is None or not checker.available or checker.dismissed:
            return "", ""
        latest = checker.latest
        ago = released_ago(latest.published_at)
        text = f"Update available: {checker.current} → {latest.version}"
        return "available", text + (f" · {ago}" if ago else "")

    def refresh(self) -> None:
        """Update the banner and the About box from the checker and the updater."""
        state, text = self._banner_state()
        self._paint_banner(state, text)
        self._paint_about()
        updater = self.updater
        if updater is not None and updater.state == "done" and not self._announced_result:
            self._announced_result = True
            try:
                self._bridge.notice(updater.message, "green")
            except Exception:
                pass

    def _paint_banner(self, state: str, text: str) -> None:
        """Show, hide or recolour the banner."""
        if not state:
            if self.banner.winfo_manager():
                self.banner.pack_forget()
            self.state = ""
            return
        bg, fg, icon = STATES[state]
        name, variant = icon.split(":")
        for widget in (self._inner, self._text_box, self._buttons):
            widget.configure(bg=bg)
        image = self.th.icon(name, variant)
        self._icon.configure(image=image or "", bg=bg)
        self._text.configure(text=text, bg=bg, fg=fg, font=self.th.font(12, bold=True))
        if state != self.state:
            for button in self._buttons.winfo_children():
                button.pack_forget()
            wanted = {
                "available": (self._b_update, self._b_release, self._b_close),
                "running": (),
                "done": (self._b_close,),
                "failed": (self._b_howto, self._b_close),
            }[state]
            for button in reversed(wanted):
                button.pack(side="right", padx=(self.th.px(6), 0))
            self._one_row = None
        if not self.banner.winfo_manager():
            self.banner.pack(fill="x", before=self._before)
        self.state = state
        self.root.after_idle(self._relayout)

    def _relayout(self) -> None:
        """Text and buttons on one row if they fit, else the buttons below."""
        th = self.th
        width = self._inner.winfo_width()
        needed = self._text_box.winfo_reqwidth() + self._buttons.winfo_reqwidth() + th.px(30)
        one_row = width <= 1 or width >= needed
        self._text.configure(wraplength=max(width - th.px(60), 200) if not one_row else 0)
        if one_row == self._one_row:
            return
        self._one_row = one_row
        if one_row:
            self._text_box.grid(row=0, column=0, sticky="w")
            self._buttons.grid(row=0, column=1, sticky="e")
        else:
            self._text_box.grid(row=0, column=0, columnspan=2, sticky="w")
            self._buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(th.px(6), 0))

    def _paint_about(self) -> None:
        """Update the Updates box's texts and buttons."""
        checker = self._checker()
        updater = self.updater
        available = bool(checker and checker.available)
        if checker is None:
            status, color = "Update checks aren't running.", t.MUTED
        elif updater is not None and updater.state == "running":
            status, color = f"Updating to {updater.target}… {updater.step}", "#5a4500"
        elif updater is not None and updater.state in ("done", "failed"):
            color = "#27500a" if updater.state == "done" else "#8b1a1a"
            status = updater.message
        elif available:
            status = f"{checker.latest.version} is available (you have {checker.current})."
            color = "#0f3f52"
        elif checker.latest is not None:
            status, color = f"You're on the latest version ({checker.current}).", "#27500a"
        elif not checker.enabled:
            status, color = "Update checks are off (turn them on in Advanced).", t.MUTED
        else:
            status, color = "Not checked yet.", t.MUTED
        self._about_status.configure(text=status, fg=color, wraplength=420)
        detail = checked_text(checker.checked_at) if checker else ""
        if checker is not None and checker.error:
            detail += f" · last check failed: {checker.error}"
        self._about_checked.configure(text=detail)
        if checker is not None and checker.checking:
            self._a_check.set_text("Checking…")
        else:
            self._a_check.set_text("Check now")
        show_update = available and (updater is None or updater.state != "running")
        show_update = show_update and not (updater is not None and updater.state == "done")
        if show_update and not self._a_update.winfo_manager():
            self._a_update.pack(side="right", padx=(0, self.th.px(6)))
        elif not show_update and self._a_update.winfo_manager():
            self._a_update.pack_forget()
        show_steps = available or (updater is not None and updater.state == "failed")
        if show_steps and not self._steps.winfo_manager():
            self._steps_note.pack(fill="x", pady=(0, self.th.px(4)))
            self._steps.pack(fill="x")
        elif not show_steps and self._steps.winfo_manager():
            self._steps_note.pack_forget()
            self._steps.pack_forget()
