"""Colours, fonts and sizes for the settings window (Habbo forum look, yellow accent).

The palette follows G-ExtensionStore's forum styling: black outlines, chunky buttons
with a darker bottom edge, light panels and a Habbo-yellow header.

Attributes:
    YELLOW (str): Header and Save button.
    YELLOW_DARK (str): Bottom edge of yellow buttons.
    INK (str): Text on yellow.
    PANEL (str): Window background.
    SIDEBAR (str): Sidebar and footer background.
    WHITE (str): Box background.
    LINE (str): Thin row separators.
    BLACK (str): Outlines.
    TEXT (str): Body text.
    MUTED (str): Help text.
    GREEN (str): ON state.
    RED (str): OFF state and errors.
    BLUE (str): The GitHub button and selected chips' outline.
    BLUE_DARK (str): Links and selected chips' text.
    GREY_BTN (str): Revert and other plain buttons.
    CHIP (str): Selected chip background.
    GITHUB_URL (str): The project's GitHub page.
    DOCS_URL (str): The project's documentation.
    ICON_DIR (Path): The Tabler icon PNGs (made by ``scripts/make_icons.py``).
    ICON_SIZES (dict[str, int]): Icon size in design pixels per variant: ``nav``
        (sidebar), ``box`` (box headers), ``link`` (footer) and ``white`` (blue button).
"""

from __future__ import annotations

import base64
import logging
import sys
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from typing import Any

YELLOW = "#ffcc00"
YELLOW_DARK = "#e0b000"
INK = "#3a2a00"
PANEL = "#dfdfd6"
SIDEBAR = "#c9c7bf"
WHITE = "#ffffff"
LINE = "#e3e1d8"
BLACK = "#000000"
TEXT = "#111111"
MUTED = "#6f6f69"
GREEN = "#5cb85c"
RED = "#d9534f"
BLUE = "#2792b9"
BLUE_DARK = "#185c75"
GREY_BTN = "#d1d1d1"
CHIP = "#effeff"

GITHUB_URL = "https://github.com/aaimang/bobba-buddy"
DOCS_URL = "https://aaimang.github.io/bobba-buddy/"

ICON_DIR = Path(__file__).resolve().parent.parent / "assets" / "icons"
ICON_SIZES = {"nav": 18, "box": 16, "link": 14, "white": 14}

log = logging.getLogger(__name__)

_FALLBACK = {"darwin": "Helvetica Neue", "win32": "Segoe UI"}


def enable_dpi_awareness() -> None:
    """On Windows, ask for real pixels so the window isn't blurry; elsewhere a no-op.

    Must run before the first ``tk.Tk()``.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # older Windows or no shcore: blurry but working
        pass


def _ns_app(selector: bytes, arg: Any) -> None:
    """Send one message to ``NSApp`` through the Objective-C runtime (macOS only).

    Args:
        selector: The selector, e.g. ``b"activateIgnoringOtherApps:"``.
        arg: Its one argument: a ``bool``, or an ``int`` for an ``NSInteger``.
    """
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    p = ctypes.c_void_p
    arg_type = ctypes.c_bool if isinstance(arg, bool) else ctypes.c_long
    send = ctypes.CFUNCTYPE(p, p, p)(("objc_msgSend", objc))
    send_arg = ctypes.CFUNCTYPE(p, p, p, arg_type)(("objc_msgSend", objc))
    app = send(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))
    send_arg(app, objc.sel_registerName(selector), arg)


def activate_app() -> None:
    """On macOS, make this process the active app so its window comes to the front.

    The bot runs as a background process started by G-Earth, so without this the
    window opens behind G-Earth (``[NSApp activateIgnoringOtherApps:YES]``).
    Elsewhere a no-op.
    """
    if sys.platform != "darwin":
        return
    try:
        _ns_app(b"setActivationPolicy:", 0)  # regular: Dock icon, can be frontmost
        _ns_app(b"activateIgnoringOtherApps:", True)
    except Exception:  # best effort: the window still opens, maybe behind
        pass


def hide_from_dock() -> None:
    """On macOS, remove the Dock icon Tk left behind once the window is closed.

    The bot keeps running in the background, so it shouldn't look like an open app.
    Elsewhere a no-op.
    """
    if sys.platform != "darwin":
        return
    try:
        _ns_app(b"setActivationPolicy:", 1)  # accessory: no Dock icon
    except Exception:
        pass


def load_png(root: tk.Misc, path: Path) -> tk.PhotoImage | None:
    """A PNG as a Tk image, or ``None`` if it can't be made.

    The file is read here and handed to Tk as data, so Tcl never opens it. On
    Windows, creating an image occasionally fails with an empty ``TclError`` (seen in
    CI after several windows in one process); one retry, then ``None`` so the window
    still opens, just without that picture.

    Args:
        root: The Tk root.
        path: The PNG file.

    Returns:
        The image, or ``None``.
    """
    try:
        data = base64.b64encode(path.read_bytes())
    except OSError:
        log.warning("image not found: %s", path)
        return None
    for attempt in (1, 2):
        try:
            return tk.PhotoImage(master=root, data=data)
        except tk.TclError as exc:
            log.warning("couldn't load %s (attempt %d): %r", path.name, attempt, exc)
    return None


class Theme:
    """Fonts and pixel scaling for one Tk root.

    Sizes are given in macOS-style pixels and converted to points, so text looks the
    same size on every OS and follows Windows/Linux display scaling.

    Attributes:
        scale: Pixels per design pixel (1.0 on macOS and 100% displays).
        family: The font family in use.
    """

    def __init__(self, root: tk.Misc) -> None:
        """Pick a font family and work out the display scale.

        Args:
            root: The Tk root.
        """
        dpi = root.winfo_fpixels("1i")
        self.scale = 1.0 if sys.platform == "darwin" else max(1.0, dpi / 96)
        families = set(tkfont.families(root))
        wanted = ["Ubuntu", _FALLBACK.get(sys.platform, "DejaVu Sans")]
        self.family = next((f for f in wanted if f in families), "TkDefaultFont")
        self._fonts: dict[tuple[int, bool], tkfont.Font] = {}
        self._root = root
        self._icons: dict[tuple[str, str], tk.PhotoImage | None] = {}

    def icon(self, name: str, variant: str = "box") -> tk.PhotoImage | None:
        """A cached Tabler icon, double size on scaled displays.

        Args:
            name: Tabler icon name, e.g. ``"users"``; ``""`` for none.
            variant: ``nav``, ``box``, ``link`` or ``white`` (colour and size).

        Returns:
            The image, or ``None`` if there's no such icon or it can't be loaded.
        """
        if not name:
            return None
        key = (name, variant)
        if key not in self._icons:
            size = ICON_SIZES[variant] * (2 if self.scale >= 1.75 else 1)
            path = ICON_DIR / f"{name}-{variant}-{size}.png"
            self._icons[key] = load_png(self._root, path) if path.exists() else None
        return self._icons[key]

    def px(self, n: float) -> int:
        """Design pixels to screen pixels.

        Args:
            n: Size in design pixels.

        Returns:
            Screen pixels, at least 1 for a non-zero size.
        """
        return max(1, round(n * self.scale)) if n else 0

    def font(self, size: int = 13, bold: bool = False) -> tkfont.Font:
        """A cached font.

        Args:
            size: Size in design pixels.
            bold: Bold weight.

        Returns:
            The font.
        """
        key = (size, bold)
        if key not in self._fonts:
            points = size if sys.platform == "darwin" else round(size * 0.75)
            self._fonts[key] = tkfont.Font(
                family=self.family, size=points, weight="bold" if bold else "normal"
            )
        return self._fonts[key]
