"""Render the settings window's Tabler icons to PNGs (run only when icons change).

Tk 8.6 can't draw SVG or icon fonts, so the window uses small PNGs. This script
downloads the outline icons from Tabler (MIT licence, pinned version), colours them
for where they're shown, and writes ``src/bobba_buddy/assets/icons/<name>-<variant>-<size>.png``
in a normal and a double size (for scaled Windows/Linux displays).

It needs PyMuPDF, which isn't a project dependency::

    uv run --no-project --with pymupdf python scripts/make_icons.py

Attributes:
    TABLER_VERSION (str): The ``@tabler/icons`` release the SVGs come from.
    OUT (Path): Where the PNGs go.
    VARIANTS (dict[str, tuple[str, int]]): Colour and base size per place in the window.
    ICONS (dict[str, tuple[str, ...]]): Icon names per variant.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

TABLER_VERSION = "3.19.0"
OUT = Path(__file__).resolve().parents[1] / "src" / "bobba_buddy" / "assets" / "icons"

VARIANTS = {
    "nav": ("#2a3a42", 18),  # sidebar
    "box": ("#3a2a00", 16),  # yellow box headers
    "link": ("#185c75", 14),  # footer link
    "white": ("#ffffff", 14),  # on the blue GitHub button
    "tool": ("#2a3a42", 14),  # toolbar buttons and labels on the Extension Logs page
}

PAGE_ICONS = (
    "users",
    "message-circle",
    "walk",
    "door",
    "sparkles",
    "coin",
    "adjustments",
    "terminal-2",
)
BOX_ICONS = (
    "users",
    "message-question",
    "flame",
    "bulb",
    "mood-happy",
    "route",
    "dice",
    "copy",
    "zzz",
    "clock",
    "refresh",
    "arrow-back-up",
    "sparkles",
    "brain",
    "coin",
    "gauge",
    "messages",
    "shield-check",
    "receipt",
    "home",
    "info-circle",
    "terminal",
)
TOOL_ICONS = ("filter", "search", "arrow-bar-to-down", "bug", "copy", "trash", "file-text")
ICONS = {
    "nav": (*PAGE_ICONS, "info-circle"),
    "box": BOX_ICONS,
    "link": ("brand-github",),
    "white": ("brand-github",),
    "tool": TOOL_ICONS,
}


def fetch(name: str) -> str:
    """Download one outline icon.

    Args:
        name: Tabler icon name, e.g. ``"users"``.

    Returns:
        The SVG text.
    """
    url = f"https://cdn.jsdelivr.net/npm/@tabler/icons@{TABLER_VERSION}/icons/outline/{name}.svg"
    with urllib.request.urlopen(url, timeout=20) as resp:  # noqa: S310 - fixed https URL
        return resp.read().decode("utf-8")


def render(svg: str, color: str, size: int, path: Path) -> None:
    """Write one coloured PNG.

    Args:
        svg: The SVG text (stroke ``currentColor``).
        color: Stroke colour, ``#rrggbb``.
        size: Width and height in pixels.
        path: Output file.
    """
    import pymupdf

    doc = pymupdf.open(stream=svg.replace("currentColor", color).encode(), filetype="svg")
    page = doc[0]
    zoom = size / page.rect.width
    page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).save(path)


def main() -> int:
    """Download and render every icon.

    Returns:
        The exit code.
    """
    OUT.mkdir(parents=True, exist_ok=True)
    cache: dict[str, str] = {}
    count = 0
    for variant, names in ICONS.items():
        color, size = VARIANTS[variant]
        for name in names:
            svg = cache.setdefault(name, fetch(name))
            for scale in (1, 2):
                render(svg, color, size * scale, OUT / f"{name}-{variant}-{size * scale}.png")
                count += 1
    print(f"wrote {count} icons to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
