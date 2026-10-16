"""Turn model output into Habbo-safe chat bubbles.

Habbo caps each bubble at 100 characters. The AIR client is ActionScript, which
counts string length in UTF-16 code units, so lengths here are measured the same way
(an emoji counts as 2).

Attributes:
    ELLIPSIS (str): Appended to the last bubble when text had to be cut short.
"""

from __future__ import annotations

import re

ELLIPSIS = "…"

_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LINE_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|>\s*|[-*•]\s+)")
_MD_MARKS = re.compile(r"\*\*|__|~~|`+|\*")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_SPACES = re.compile(r"\s+")
_FORBIDDEN_LEAD = ("/", ":")


def ulen(text: str) -> int:
    """Length in UTF-16 code units, which is how the Habbo client counts.

    Args:
        text: Any text.

    Returns:
        The number of UTF-16 code units (an emoji counts as 2).
    """
    return len(text.encode("utf-16-le")) // 2


def strip_command_lead(text: str) -> str:
    """Drop leading ``/`` or ``:`` so a bubble can never be read as a chat command.

    Args:
        text: A bubble or reply.

    Returns:
        The text without leading whitespace, ``/`` or ``:``.
    """
    text = text.lstrip()
    while text.startswith(_FORBIDDEN_LEAD):
        text = text[1:].lstrip()
    return text


def sanitize(text: str) -> str:
    """Flatten markdown, drop links and control chars, and collapse to a single line.

    Args:
        text: Raw model output.

    Returns:
        Plain, single-line text that never starts with ``/`` or ``:``.
    """
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    lines = [_LINE_PREFIX.sub("", line) for line in text.splitlines()]
    text = " ".join(lines)
    text = _MD_MARKS.sub("", text)
    text = _CONTROL.sub(" ", text)
    text = _SPACES.sub(" ", text).strip()
    return strip_command_lead(text)


def _hard_split(word: str, limit: int) -> list[str]:
    """Split a word longer than a bubble into bubble-sized pieces.

    Args:
        word: A word with no spaces.
        limit: Maximum UTF-16 units per piece.

    Returns:
        Pieces of at most ``limit`` units, in order.
    """
    pieces: list[str] = []
    current = ""
    for ch in word:
        if ulen(current + ch) > limit:
            pieces.append(current)
            current = ch
        else:
            current += ch
    if current:
        pieces.append(current)
    return pieces


def _with_ellipsis(text: str, limit: int) -> str:
    """Trim text so text + ``…`` fits the limit, preferring a word boundary.

    Args:
        text: The last bubble that will be kept.
        limit: Maximum UTF-16 units in the result.

    Returns:
        The trimmed text ending in ``ELLIPSIS``.
    """
    if ulen(text) + 1 <= limit:
        return text.rstrip(" ,;:.") + ELLIPSIS
    cut = text
    while cut and ulen(cut) + 1 > limit:
        cut = cut[:-1]
    space = cut.rfind(" ")
    if space >= len(cut) // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.") + ELLIPSIS


def split_bubbles(text: str, limit: int, max_bubbles: int) -> list[str]:
    """Split text into chat bubbles.

    Breaks at spaces, hard-splits words longer than a bubble, and ends with ``…``
    when the text had to be cut short. No bubble starts with ``/`` or ``:``.

    Args:
        text: Sanitized single-line text.
        limit: Maximum UTF-16 units per bubble (100 in Habbo; less for whispers,
            whose target name shares the budget).
        max_bubbles: Maximum number of bubbles.

    Returns:
        At most ``max_bubbles`` non-empty bubbles.

    Raises:
        ValueError: If ``limit`` or ``max_bubbles`` is below 1.
    """
    if limit < 1 or max_bubbles < 1:
        raise ValueError("limit and max_bubbles must be >= 1")

    bubbles: list[str] = []
    current = ""
    for word in text.split():
        for piece in _hard_split(word, limit) if ulen(word) > limit else [word]:
            if not current:
                current = piece
            elif ulen(current) + 1 + ulen(piece) <= limit:
                current = f"{current} {piece}"
            else:
                bubbles.append(current)
                current = piece
    if current:
        bubbles.append(current)

    if len(bubbles) > max_bubbles:
        bubbles = bubbles[:max_bubbles]
        bubbles[-1] = _with_ellipsis(bubbles[-1], limit)

    # A later bubble could start with ':' or '/' (e.g. ":)"); never send that.
    cleaned = [strip_command_lead(b) for b in bubbles]
    return [b for b in cleaned if b]
