"""Exception guard for G-Python callbacks, and reporting of packets that don't parse.

G-Python runs every intercept callback on one thread with no exception handling: an
uncaught error kills that thread and stops all packet flow. Every callback we register
goes through :func:`guarded`.

A packet that doesn't match the expected layout (ProtocolError) is a data problem, not
a bug: the first one per handler is logged as a warning with a short hex dump and saved
whole to the dump folder (logs/bad-packets/), and repeats only go to the debug log. That
keeps the log readable while capturing enough to work out a layout change.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .protocol import ProtocolError

log = logging.getLogger(__name__)

_DUMP_BYTES = 400
_reported: Counter[str] = Counter()
_reported_lock = threading.Lock()
_dump_dir: Path | None = None


def configure_dump_dir(path: Path | None) -> None:
    """Set where full copies of bad packets are saved.

    Args:
        path: Folder for ``<name>-<time>.hex`` dumps, or ``None`` to turn saving off.
    """
    global _dump_dir
    _dump_dir = path


def _save_packet(name: str, raw: bytes, problem: str) -> Path | None:
    """Write a whole packet as hex to the dump folder.

    Args:
        name: Packet or handler name, used in the file name.
        raw: The complete packet bytes.
        problem: Why it didn't parse, written as a comment line.

    Returns:
        The file written, or ``None`` if saving is off, there's nothing to save, or
        the write failed.
    """
    if _dump_dir is None or not raw:
        return None
    try:
        _dump_dir.mkdir(parents=True, exist_ok=True)
        path = _dump_dir / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.hex"
        lines = [f"# {name}: {problem}", f"# {len(raw)} bytes"]
        lines += [raw[i : i + 32].hex(" ") for i in range(0, len(raw), 32)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path
    except OSError as exc:
        log.debug("could not save bad packet: %s", exc)
        return None


def hex_dump(data: bytes, limit: int = _DUMP_BYTES) -> str:
    """Compact dump: hex, then printable ASCII so strings in the packet are readable.

    Args:
        data: Bytes to dump.
        limit: Maximum number of bytes shown.

    Returns:
        ``"<hex bytes> | <ascii>"``, noting how many bytes were left out.
    """
    chunk = bytes(data[:limit])
    ascii_view = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
    more = f" (+{len(data) - limit} more bytes)" if len(data) > limit else ""
    return f"{chunk.hex(' ')}{more} | {ascii_view}"


def report_bad_packet(name: str, packet: Any, problem: str) -> None:
    """Log a packet that didn't parse: once with a dump, then only at debug level.

    The first report per ``name`` (per :func:`reset_reports`) is a warning with a hex
    excerpt, and the whole packet is saved to the dump folder.

    Args:
        name: Packet or handler name, e.g. ``"Users"``.
        packet: The ``HPacket`` (anything with a ``bytearray``), or ``None``.
        problem: Why it didn't parse.
    """
    with _reported_lock:
        _reported[name] += 1
        count = _reported[name]
    raw = bytes(getattr(packet, "bytearray", b""))
    if count == 1:
        header = int.from_bytes(raw[4:6], "big") if len(raw) >= 6 else -1
        saved = _save_packet(name, raw, problem)
        log.warning(
            "%s packet didn't match the expected layout (%s); skipped what didn't parse. "
            "header=%d length=%d%s bytes: %s",
            name,
            problem,
            header,
            len(raw),
            f" saved={saved}" if saved else "",
            hex_dump(raw),
        )
    else:
        log.debug("%s packet didn't parse (%s), %d so far", name, problem, count)


def reset_reports() -> None:
    """Start counting afresh (new G-Earth session), so the next odd packet is dumped again."""
    with _reported_lock:
        _reported.clear()


def guarded(fn: Callable[..., Any]) -> Callable[..., None]:
    """Wrap a callback so no exception escapes into G-Python's packet or event threads.

    ``ProtocolError`` is reported with :func:`report_bad_packet` (the first argument's
    ``packet`` is dumped); any other exception is logged with its traceback.

    Args:
        fn: The callback (a function or ``functools.partial``).

    Returns:
        A wrapper with the same name that never raises.
    """
    name = getattr(fn, "__name__", None) or getattr(
        getattr(fn, "func", None), "__name__", "handler"
    )

    def wrapper(*args: Any) -> None:
        """Call ``fn`` and swallow (but log) any exception."""
        try:
            fn(*args)
        except ProtocolError as exc:
            packet = getattr(args[0], "packet", None) if args else None
            report_bad_packet(name, packet, str(exc))
        except Exception:
            log.exception("handler %s failed", name)

    wrapper.__name__ = name
    return wrapper
