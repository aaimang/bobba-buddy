"""Habbo-style building blocks for the settings window, made from plain ``tk`` widgets.

Plain ``tk`` (not ``ttk``) because only it lets every colour be set on every OS; on
macOS a ``tk.Button`` ignores its background, so buttons here are ``Label``s with
click bindings. Every control has ``get()`` and ``set(value)``; ``set`` never calls
the change callback.
"""

from __future__ import annotations

import sys
import tkinter as tk
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from . import theme as t
from .theme import Theme

Callback = Callable[[Any], None]


def _clickable(widgets: Sequence[tk.Misc], handler: Callable[[], None]) -> None:
    """Call ``handler`` when any of the widgets is clicked, with a hand cursor."""
    for w in widgets:
        w.configure(cursor="hand2")
        w.bind("<Button-1>", lambda _e: handler())


class Pill(tk.Frame):
    """An ON/OFF switch: two halves, the active one coloured."""

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        value: bool,
        on_change: Callback,
        *,
        labels: tuple[str, str] = ("ON", "OFF"),
        size: int = 11,
    ) -> None:
        """Create the switch.

        Args:
            parent: Parent widget.
            th: Theme.
            value: Starting state.
            on_change: Called with the new state after a click.
            labels: Text of the on and off halves.
            size: Font size in design pixels.
        """
        super().__init__(parent, bg=t.BLACK, padx=th.px(2), pady=th.px(2))
        font = th.font(size, bold=True)
        pad = (th.px(9), th.px(2))
        self._on = tk.Label(self, text=labels[0], font=font, padx=pad[0], pady=pad[1])
        self._off = tk.Label(self, text=labels[1], font=font, padx=pad[0], pady=pad[1])
        self._on.pack(side="left")
        self._off.pack(side="left", padx=(th.px(2), 0))
        self._on_change = on_change
        _clickable((self, self._on, self._off), self.toggle)
        self.set(value)

    def toggle(self) -> None:
        """Flip the state and report it."""
        self.set(not self._value)
        self._on_change(self._value)

    def get(self) -> bool:
        """The current state."""
        return self._value

    def set(self, value: Any) -> None:
        """Show a state without reporting it.

        Args:
            value: The state.
        """
        self._value = bool(value)
        if self._value:
            self._on.configure(bg=t.GREEN, fg=t.WHITE)
            self._off.configure(bg=t.WHITE, fg="#999999")
        else:
            self._on.configure(bg=t.WHITE, fg="#999999")
            self._off.configure(bg=t.RED, fg=t.WHITE)


class Stepper(tk.Frame):
    """A number with − and + buttons; typing works too, and the value is clamped."""

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        value: float,
        on_change: Callback,
        *,
        minimum: float | None = None,
        maximum: float | None = None,
        step: float = 1,
        decimals: int = 0,
        unit: str = "",
    ) -> None:
        """Create the stepper.

        Args:
            parent: Parent widget.
            th: Theme.
            value: Starting value.
            on_change: Called with the new value (int, or float with ``decimals``).
            minimum: Lowest value, or ``None``.
            maximum: Highest value, or ``None``.
            step: Change per click.
            decimals: Decimals shown; 0 means whole numbers.
            unit: ``$`` goes before the number, anything else after it.
        """
        super().__init__(parent, bg=t.BLACK, padx=th.px(2), pady=th.px(2))
        self._min, self._max, self._step = minimum, maximum, step
        self._decimals, self._unit = decimals, unit
        self._on_change = on_change
        self._repeat: str | None = None
        font, bold = th.font(12), th.font(13, bold=True)
        minus = tk.Label(self, text="−", font=bold, bg="#e5e5e0", fg=t.TEXT, padx=th.px(7))
        plus = tk.Label(self, text="+", font=bold, bg="#e5e5e0", fg=t.TEXT, padx=th.px(7))
        self._entry = tk.Entry(
            self,
            width=7,
            justify="center",
            font=font,
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=t.WHITE,
            fg=t.TEXT,
            insertbackground=t.TEXT,
        )
        minus.pack(side="left", fill="y")
        self._entry.pack(side="left", fill="y", padx=th.px(2), ipady=th.px(2))
        plus.pack(side="left", fill="y")
        for button, sign in ((minus, -1), (plus, 1)):
            button.configure(cursor="hand2")
            button.bind("<ButtonPress-1>", lambda _e, s=sign: self._press(s))
            button.bind("<ButtonRelease-1>", lambda _e: self._release())
        self._entry.bind("<Return>", lambda _e: self._commit())
        self._entry.bind("<FocusOut>", lambda _e: self._commit())
        self._entry.bind("<Up>", lambda _e: self.nudge(1))
        self._entry.bind("<Down>", lambda _e: self.nudge(-1))
        self.set(value)

    def _clamp(self, value: float) -> float | int:
        """Clamp to the limits and round to the shown precision."""
        if self._min is not None:
            value = max(self._min, value)
        if self._max is not None:
            value = min(self._max, value)
        return round(value, self._decimals) if self._decimals else int(round(value))

    def _text(self, value: float) -> str:
        """Format a value with its unit."""
        number = f"{value:.{self._decimals}f}"
        if self._unit == "$":
            return f"${number}"
        return f"{number}{self._unit}"

    def get(self) -> float | int:
        """The current value."""
        return self._value

    def set(self, value: Any) -> None:
        """Show a value without reporting it.

        Args:
            value: The value; clamped to the limits.
        """
        self._value = self._clamp(float(value))
        self._entry.delete(0, "end")
        self._entry.insert(0, self._text(self._value))

    def _update(self, value: float) -> None:
        """Set a value and report it if it changed."""
        old = self._value
        self.set(value)
        if self._value != old:
            self._on_change(self._value)

    def nudge(self, direction: int) -> str:
        """Step the value up (1) or down (-1).

        Args:
            direction: 1 or -1.

        Returns:
            ``"break"``, so arrow keys don't also move the cursor.
        """
        self._update(self._value + direction * self._step)
        return "break"

    def _press(self, direction: int) -> None:
        """Step once, then keep stepping while the button is held."""
        self.nudge(direction)
        self._repeat = self.after(400, self._hold, direction)

    def _hold(self, direction: int) -> None:
        """Repeat while held."""
        self.nudge(direction)
        self._repeat = self.after(70, self._hold, direction)

    def _release(self) -> None:
        """Stop repeating."""
        if self._repeat is not None:
            self.after_cancel(self._repeat)
            self._repeat = None

    def _commit(self) -> None:
        """Take a typed value, or put the old one back if it isn't a number."""
        text = self._entry.get().replace("$", "").replace(",", ".").strip()
        if self._unit and self._unit != "$":
            text = text.removesuffix(self._unit).strip()
        try:
            value = float(text)
        except ValueError:
            self.set(self._value)
            return
        self._update(value)


class TextField(tk.Frame):
    """A single-line text box with a black outline."""

    def __init__(
        self, parent: tk.Misc, th: Theme, value: str, on_change: Callback, *, width: int = 22
    ) -> None:
        """Create the text box.

        Args:
            parent: Parent widget.
            th: Theme.
            value: Starting text.
            on_change: Called with the new value on every edit.
            width: Width in characters.
        """
        super().__init__(parent, bg=t.BLACK, padx=th.px(2), pady=th.px(2))
        self._var = tk.StringVar(self)
        self._quiet = False
        self._on_change = on_change
        pad = tk.Frame(self, bg=t.WHITE, padx=th.px(6), pady=th.px(3))
        pad.pack()
        self.entry = tk.Entry(
            pad,
            textvariable=self._var,
            width=width,
            font=th.font(12),
            relief="flat",
            bd=0,
            highlightthickness=0,
            bg=t.WHITE,
            fg=t.TEXT,
            insertbackground=t.TEXT,
        )
        self.entry.pack()
        self.set(value)
        self._var.trace_add("write", lambda *_: self._changed())

    def _changed(self) -> None:
        """Report an edit, unless it came from :meth:`set`."""
        if not self._quiet:
            self._on_change(self.get())

    def get(self) -> Any:
        """The current text."""
        return self._var.get()

    def set(self, value: Any) -> None:
        """Show a value without reporting it.

        Args:
            value: The text.
        """
        self._quiet = True
        try:
            self._var.set(str(value))
        finally:
            self._quiet = False


class NamesField(TextField):
    """A comma-separated list of names, e.g. the whitelist."""

    def get(self) -> list[str]:
        """The names, trimmed, without empty entries."""
        return [n.strip() for n in self._var.get().split(",") if n.strip()]

    def set(self, value: Any) -> None:
        """Show a list of names without reporting it.

        Args:
            value: The names.
        """
        super().set(", ".join(value))


class Choice(tk.Frame):
    """One choice out of a few, shown side by side."""

    def __init__(
        self, parent: tk.Misc, th: Theme, value: str, on_change: Callback, options: Sequence[str]
    ) -> None:
        """Create the choice.

        Args:
            parent: Parent widget.
            th: Theme.
            value: Starting choice.
            on_change: Called with the new choice.
            options: The choices.
        """
        super().__init__(parent, bg=t.BLACK, padx=th.px(2), pady=th.px(2))
        self._labels: dict[str, tk.Label] = {}
        self._on_change = on_change
        for i, option in enumerate(options):
            label = tk.Label(self, text=option, font=th.font(11, bold=True), padx=th.px(7))
            label.pack(side="left", padx=(th.px(2) if i else 0, 0), ipady=th.px(2))
            _clickable((label,), lambda o=option: self._pick(o))
            self._labels[option] = label
        self.set(value)

    def _pick(self, option: str) -> None:
        """Select an option and report it."""
        if option != self._value:
            self.set(option)
            self._on_change(option)

    def get(self) -> str:
        """The current choice."""
        return self._value

    def set(self, value: Any) -> None:
        """Show a choice without reporting it.

        Args:
            value: The choice.
        """
        self._value = str(value)
        for option, label in self._labels.items():
            on = option == self._value
            label.configure(bg=t.YELLOW if on else t.WHITE, fg=t.INK if on else t.TEXT)


class Chips(tk.Frame):
    """Several options that can each be on or off; the value keeps the options' order."""

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        value: Sequence[str],
        on_change: Callback,
        options: Sequence[str],
    ) -> None:
        """Create the chips.

        Args:
            parent: Parent widget.
            th: Theme.
            value: The options that start on.
            on_change: Called with the list of options that are on.
            options: All options, in order.
        """
        super().__init__(parent, bg=t.WHITE)
        self._choices = list(options)
        self._chips: dict[str, tuple[tk.Frame, tk.Label]] = {}
        self._on_change = on_change
        for option in self._choices:
            border = tk.Frame(self, padx=1, pady=1)
            label = tk.Label(border, text=option, font=th.font(11), padx=th.px(9))
            label.pack(ipady=th.px(1))
            border.pack(side="left", padx=(th.px(4), 0))
            _clickable((border, label), lambda o=option: self._flip(o))
            self._chips[option] = (border, label)
        self.set(value)

    def _flip(self, option: str) -> None:
        """Turn one option on or off and report the result."""
        chosen = set(self._value)
        chosen.symmetric_difference_update({option})
        self.set([o for o in self._choices if o in chosen])
        self._on_change(self.get())

    def get(self) -> list[str]:
        """The options that are on, in order."""
        return list(self._value)

    def set(self, value: Any) -> None:
        """Show a selection without reporting it.

        Args:
            value: The options that are on.
        """
        self._value = [o for o in self._choices if o in set(value)]
        for option, (border, label) in self._chips.items():
            on = option in self._value
            border.configure(bg=t.BLUE if on else "#aaaaaa")
            label.configure(bg=t.CHIP if on else "#eeeeee", fg=t.BLUE_DARK if on else "#777777")


class ForumButton(tk.Frame):
    """A chunky forum button: black outline, thick bottom edge, hover and press shades."""

    COLORS = {
        "yellow": (t.YELLOW, "#ffd633", "#e6b800", t.INK),
        "grey": (t.GREY_BTN, "#dcdcdc", "#c2c2c2", t.TEXT),
        "blue": (t.BLUE, "#2999c2", "#2487ac", t.WHITE),
    }

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        text: str,
        command: Callable[[], Any],
        *,
        color: str = "grey",
        icon: tk.PhotoImage | None = None,
        small: bool = False,
    ) -> None:
        """Create the button.

        Args:
            parent: Parent widget.
            th: Theme.
            text: Button text.
            command: Called on click (press and release inside the button).
            color: ``yellow``, ``grey`` or ``blue``.
            icon: Optional image left of the text.
            small: Smaller text and padding, for toolbars.
        """
        super().__init__(parent, bg=t.BLACK)
        self._base, self._hover, self._down, fg = self.COLORS[color]
        self._label = tk.Label(
            self,
            text=f" {text}" if icon else text,  # Tk has no gap option for compound
            image=icon or "",
            compound="left",
            font=th.font(11 if small else 13, bold=True),
            bg=self._base,
            fg=fg,
            padx=th.px(8 if small else 14),
            pady=th.px(1 if small else 3),
            cursor="hand2",
        )
        self._label.pack(padx=th.px(2), pady=(th.px(2), th.px(3)))
        self._command = command
        self._label.bind("<Enter>", lambda _e: self._label.configure(bg=self._hover))
        self._label.bind("<Leave>", lambda _e: self._label.configure(bg=self._base))
        self._label.bind("<ButtonPress-1>", lambda _e: self._label.configure(bg=self._down))
        self._label.bind("<ButtonRelease-1>", self._release)

    def _release(self, event: tk.Event) -> None:
        """Run the command if the pointer is still over the button."""
        self._label.configure(bg=self._hover)
        inside = 0 <= event.x < self._label.winfo_width() and 0 <= event.y < (
            self._label.winfo_height()
        )
        if inside:
            self._command()

    def invoke(self) -> None:
        """Run the command, as a click would."""
        self._command()

    def set_color(self, color: str) -> None:
        """Change the colour, e.g. yellow while a toggle is on.

        Args:
            color: ``yellow``, ``grey`` or ``blue``.
        """
        self._base, self._hover, self._down, fg = self.COLORS[color]
        self._label.configure(bg=self._base, fg=fg)


class Box(tk.Frame):
    """A section box: black outline, yellow header, white rows."""

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        title: str,
        note: str = "",
        icon: tk.PhotoImage | None = None,
    ) -> None:
        """Create the box.

        Args:
            parent: Parent widget.
            th: Theme.
            title: Header text.
            note: Optional grey line under the header.
            icon: Optional image left of the title.
        """
        super().__init__(parent, bg=t.BLACK)
        self._th = th
        inner = tk.Frame(self, bg=t.WHITE)
        inner.pack(fill="both", expand=True, padx=th.px(2), pady=(th.px(2), th.px(3)))
        header = tk.Frame(inner, bg=t.YELLOW, padx=th.px(10), pady=th.px(5))
        header.pack(fill="x")
        if icon is not None:
            tk.Label(header, image=icon, bg=t.YELLOW).pack(side="left", padx=(0, th.px(6)))
        tk.Label(
            header,
            text=title,
            font=th.font(13, bold=True),
            bg=t.YELLOW,
            fg=t.INK,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Frame(inner, bg=t.BLACK, height=th.px(2)).pack(fill="x")
        self.body = tk.Frame(inner, bg=t.WHITE)
        self.body.pack(fill="both", expand=True)
        self._rows = 0
        if note:
            tk.Label(
                self.body,
                text=note,
                font=th.font(11),
                bg=t.WHITE,
                fg=t.MUTED,
                anchor="w",
                padx=th.px(10),
                pady=th.px(4),
            ).pack(fill="x")
            self._rows = 1

    def row(self, label: str, help: str = "") -> tuple[tk.Frame, tk.Label, tk.Frame]:
        """Add a row: a label (with optional help) on the left, a control on the right.

        Args:
            label: The setting's name.
            help: Grey text under the name.

        Returns:
            The frame to put the control in, the name label, and the row itself.
        """
        th = self._th
        if self._rows:
            tk.Frame(self.body, bg=t.LINE, height=1).pack(fill="x")
        self._rows += 1
        row = tk.Frame(self.body, bg=t.WHITE, padx=th.px(10), pady=th.px(6))
        row.pack(fill="x")
        holder = tk.Frame(row, bg=t.WHITE)
        holder.pack(side="right", padx=(th.px(10), 0))
        text = tk.Frame(row, bg=t.WHITE)
        text.pack(side="left", fill="x", expand=True)
        name = tk.Label(text, text=label, font=th.font(13), bg=t.WHITE, fg=t.TEXT, anchor="w")
        name.pack(fill="x")
        if help:
            tk.Label(text, text=help, font=th.font(11), bg=t.WHITE, fg=t.MUTED, anchor="w").pack(
                fill="x"
            )
        return holder, name, row


class Sidebar(tk.Frame):
    """The page list on the left; the selected page gets a white row and a yellow bar."""

    def __init__(
        self,
        parent: tk.Misc,
        th: Theme,
        names: Sequence[str],
        on_select: Callable[[str], None],
        icons: Mapping[str, tk.PhotoImage | None] | None = None,
    ) -> None:
        """Create the sidebar.

        Args:
            parent: Parent widget.
            th: Theme.
            names: Page names, top to bottom.
            on_select: Called with a page name when it's clicked.
            icons: Optional image per page name, shown left of the name.
        """
        super().__init__(parent, bg=t.SIDEBAR, width=th.px(160))
        self._items: dict[str, tuple[tk.Misc, ...]] = {}
        for name in names:
            item = tk.Frame(self, bg=t.SIDEBAR)
            item.pack(fill="x")
            bar = tk.Frame(item, bg=t.SIDEBAR, width=th.px(4))
            bar.pack(side="left", fill="y")
            parts: list[tk.Misc] = [item]
            icon = (icons or {}).get(name)
            if icon is not None:
                image = tk.Label(item, image=icon, bg=t.SIDEBAR)
                image.pack(side="left", padx=(th.px(8), 0))
                parts.append(image)
            label = tk.Label(
                item,
                text=name,
                font=th.font(13, bold=True),
                bg=t.SIDEBAR,
                fg="#2a3a42",
                anchor="w",
                padx=th.px(8 if icon is not None else 10),
                pady=th.px(7),
            )
            label.pack(side="left", fill="x", expand=True)
            parts.append(label)
            tk.Frame(self, bg="#b9b7af", height=1).pack(fill="x")
            _clickable(parts, lambda n=name: on_select(n))
            self._items[name] = (bar, *parts)

    def select(self, name: str) -> None:
        """Highlight a page.

        Args:
            name: The page to highlight.
        """
        for page, (bar, *parts) in self._items.items():
            on = page == name
            for part in parts:
                part.configure(bg=t.WHITE if on else t.SIDEBAR)
            bar.configure(bg=t.YELLOW if on else t.SIDEBAR)


class ScrollArea(tk.Frame):
    """A vertically scrolling area; the scrollbar only shows when it's needed."""

    def __init__(self, parent: tk.Misc, bg: str) -> None:
        """Create the area; put content in ``inner``.

        Args:
            parent: Parent widget.
            bg: Background colour.
        """
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._scrolled)
        self.inner = tk.Frame(self.canvas, bg=bg)
        window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.canvas.pack(side="left", fill="both", expand=True)

    def _scrolled(self, first: str, last: str) -> None:
        """Move the scrollbar, and hide it when everything fits."""
        self._bar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self._bar.pack_forget()
        elif not self._bar.winfo_ismapped():
            self._bar.pack(side="right", fill="y", before=self.canvas)

    def wheel(self, event: tk.Event) -> None:
        """Scroll for a mouse-wheel or trackpad event, on any OS.

        Args:
            event: ``<MouseWheel>`` (macOS, Windows) or ``<Button-4/5>`` (Linux).
        """
        if self.canvas.yview() == (0.0, 1.0):
            return
        if getattr(event, "num", None) in (4, 5):
            units = -1 if event.num == 4 else 1
        elif sys.platform == "darwin":
            units = -event.delta
        else:
            units = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        self.canvas.yview_scroll(units, "units")

    def to_top(self) -> None:
        """Scroll back to the top."""
        self.canvas.yview_moveto(0)

    def show(self, widget: tk.Misc) -> None:
        """Scroll so a widget inside the area is visible.

        Args:
            widget: A descendant of ``inner``.
        """
        self.update_idletasks()
        height = max(self.inner.winfo_height(), 1)
        y = widget.winfo_rooty() - self.inner.winfo_rooty()
        self.canvas.yview_moveto(max(0.0, (y - 20) / height))
