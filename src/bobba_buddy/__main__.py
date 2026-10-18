"""Entry point: ``python -m bobba_buddy -p 9092``.

Standalone, it reconnects to G-Earth with backoff. When G-Earth launches it (it passes
``-c <cookie>``), it exits on disconnect and lets G-Earth manage its lifecycle.

Usage::

    uv run python -m bobba_buddy -p 9092
    uv run python -m bobba_buddy -p 9092 --project-dir ~/Desktop/bobba-buddy

Attributes:
    BACKOFF_MAX_S (float): Longest wait between reconnection attempts.
    STABLE_SESSION_S (float): A connection lasting this long resets the backoff.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys
import threading
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from . import updates
from .bot import EXTENSION_INFO, EXTENSION_SETTINGS, BobbaBuddy
from .budget import Budget
from .config import Config, ConfigError, ConfigStore
from .gpython import Extension
from .guard import configure_dump_dir
from .llm import ClaudeResponder
from .settings import BotBridge, SettingsHost, logbuffer

log = logging.getLogger("bobba_buddy")

BACKOFF_MAX_S = 30.0
STABLE_SESSION_S = 60.0


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the command line, including the arguments G-Earth passes.

    Args:
        argv: Arguments without the program name.

    Returns:
        ``port``, ``auth_token`` (G-Earth's cookie), ``filename``, ``project_dir``,
        ``config`` and ``log_level``.
    """
    parser = argparse.ArgumentParser(prog="bobba_buddy", description=EXTENSION_INFO["description"])
    parser.add_argument("-p", "--port", type=int, default=9092, help="G-Earth extension port")
    parser.add_argument("-c", "--auth-token", help="cookie passed by G-Earth when it launches us")
    parser.add_argument("-f", "--filename", help="passed by G-Earth when it launches us")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="folder holding .env and config.toml (default: current directory)",
    )
    parser.add_argument("--config", type=Path, default=None, help="config file path")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def setup_logging(log_dir: Path, level: str, console: bool) -> None:
    """Log to a rotating ``bobba-buddy.log`` (about 6 MB kept), and optionally to stderr.

    Args:
        log_dir: Folder for the log files.
        level: Logging level name, e.g. ``"INFO"``.
        console: Also log to stderr (off when G-Earth launches the bot, since its
            output may never be read).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "bobba-buddy.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.addHandler(file_handler)
    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)
    # The SDK's HTTP client logs every request at INFO.
    logging.getLogger("httpx2").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def gearth_args(args: argparse.Namespace) -> list[str]:
    """Rebuild the arguments g-python's ``Extension`` expects.

    Args:
        args: Parsed command line.

    Returns:
        ``["-p", port]`` plus ``-c`` and ``-f`` when G-Earth supplied them.
    """
    out = ["-p", str(args.port)]
    if args.auth_token:
        out += ["-c", args.auth_token]
    if args.filename:
        out += ["-f", args.filename]
    return out


def start_extension(ext: Extension, stop: threading.Event) -> bool:
    """``ext.start()`` with an escape hatch.

    g-python 0.1.6 blocks in ``start()`` on a barrier until G-Earth sends INIT. If
    G-Earth refuses the connection (e.g. the "allow?" prompt is declined) the socket
    closes and ``start()`` would hang forever, so watch for that and break the barrier.

    Args:
        ext: The extension to start.
        stop: Set to give up (Ctrl-C).

    Returns:
        ``True`` if connected to G-Earth.

    Raises:
        OSError: If G-Earth isn't reachable (re-raised from ``ext.start()``).
    """
    errors: list[BaseException] = []

    def run() -> None:
        """Run ``ext.start()``, keeping any exception for the caller."""
        try:
            ext.start()
        except BaseException as exc:  # noqa: BLE001 - reported to the caller
            errors.append(exc)

    thread = threading.Thread(target=run, name="gearth-start", daemon=True)
    thread.start()
    while thread.is_alive():
        thread.join(0.5)
        sock = getattr(ext, "_Extension__sock", None)
        if stop.is_set() or (sock is not None and ext.is_closed()):
            barrier = getattr(ext, "_Extension__start_barrier", None)
            if barrier is not None:
                barrier.abort()
            thread.join(1.0)
            return False
    if errors:
        raise errors[0]
    return not ext.is_closed()


def run_session(
    args,
    store: ConfigStore,
    responder: ClaudeResponder,
    budget: Budget,
    stop: threading.Event,
    settings: SettingsHost,
) -> bool:
    """Run one connection to G-Earth until it closes or ``stop`` is set.

    The main thread spends the session in
    :meth:`~bobba_buddy.settings.host.SettingsHost.serve`, which opens the settings
    window when G-Earth's ▶ button is pressed.

    Args:
        args: Parsed command line (with ``project_dir_resolved`` set).
        store: Config store; its reload callbacks are pointed at this session's bot.
        responder: Claude responder, shared across sessions.
        budget: Daily budget, shared across sessions.
        stop: Set to end the session.
        settings: Settings window host, shared across sessions.

    Returns:
        ``True`` if it connected at all.
    """
    ext = Extension(EXTENSION_INFO, gearth_args(args), EXTENSION_SETTINGS, silent=True)
    bot = BobbaBuddy(
        ext,
        store.get,
        responder,
        budget,
        last_room_path=args.project_dir_resolved / "state" / "last_room.json",
        open_settings=settings.request_open,
    )
    settings.bridge = BotBridge(bot, store)

    def on_reload(cfg: Config) -> None:
        bot.notice(f"Config reloaded ({len(cfg.whitelist)} whitelisted)", "blue")
        bot.hotel.notify("config", f"Settings saved: {len(cfg.whitelist)} on the whitelist.")

    def on_error(err: str) -> None:
        bot.notice(f"Config error, keeping old settings: {err}", "red")
        bot.hotel.notify("config", f"Config error, kept the old settings: {err}")

    store.on_reload = on_reload
    store.on_error = on_error
    bot.install()
    try:
        if not start_extension(ext, stop):
            return False
    except OSError as exc:
        log.warning("can't reach G-Earth on port %d: %s", args.port, exc)
        return False

    bot.start()
    checker = updates.current()
    if checker is not None:
        checker.announce(bot.notice)  # a version found while disconnected
    bot.notice(
        f"Bobba Buddy {EXTENSION_INFO['version']} loaded (model {store.get().claude.model})",
        "green",
    )
    log.info("connected to G-Earth on port %d", args.port)
    try:
        settings.serve(lambda: ext.is_closed() or stop.is_set())
    finally:
        bot.stop()
        if not ext.is_closed():
            try:
                ext.stop()
            except Exception:
                log.debug("ext.stop failed", exc_info=True)
    log.info("G-Earth connection closed")
    return True


def check_for_updates(
    checker: updates.UpdateChecker, settings: SettingsHost, stop: threading.Event
) -> None:
    """Background thread: ask GitHub at most once a day, announce new versions.

    Args:
        checker: The update checker.
        settings: The settings host; its bridge reaches the connected bot, if any.
        stop: Set to end the thread.
    """
    while not stop.is_set():
        checker.maybe_check()
        bridge = settings.bridge
        if bridge is not None:
            checker.announce(bridge.notice)
        stop.wait(3600)  # look again hourly; maybe_check() only asks GitHub daily


def main(argv: list[str] | None = None) -> int:
    """Start the bot: logging, ``.env``, config, then the reconnect loop.

    Args:
        argv: Command-line arguments; ``sys.argv[1:]`` if ``None``.

    Returns:
        The exit code, 0 on a clean stop or 2 if the config or API key is missing.
    """
    args = parse_args(sys.argv[1:] if argv is None else argv)
    project_dir = (args.project_dir or Path.cwd()).expanduser().resolve()
    args.project_dir_resolved = project_dir
    launched_by_gearth = args.auth_token is not None

    # When G-Earth launches us, stdout may never be drained, so log to file only.
    setup_logging(project_dir / "logs", args.log_level, console=not launched_by_gearth)
    logbuffer.install(project_dir / "logs" / "bobba-buddy.log")  # Extension Logs page
    configure_dump_dir(project_dir / "logs" / "bad-packets")
    load_dotenv(project_dir / ".env")

    config_path = (args.config or project_dir / "config.toml").expanduser().resolve()
    try:
        store = ConfigStore(config_path)
    except ConfigError as exc:
        log.error("%s (copy config.example.toml to config.toml to start)", exc)
        return 2

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is not set; add it to %s", project_dir / ".env")
        return 2

    responder = ClaudeResponder(anthropic.Anthropic(), pricing=lambda: store.get().pricing)
    budget = Budget(
        project_dir / "state" / "budget.json", limit_usd=lambda: store.get().limits.daily_budget_usd
    )

    stop = threading.Event()
    settings = SettingsHost(config_path, project_dir / "config.example.toml")
    checker = updates.install(
        project_dir / "state" / "update_check.json",
        enabled=lambda: store.get().updates.check,
    )
    threading.Thread(
        target=check_for_updates, args=(checker, settings, stop), name="buddy-update", daemon=True
    ).start()

    def handle_signal(signum, _frame) -> None:
        """Stop on SIGINT or SIGTERM."""
        log.info("received signal %d, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    backoff = 1.0
    while not stop.is_set():
        started = time.monotonic()
        connected = run_session(args, store, responder, budget, stop, settings)
        if launched_by_gearth or stop.is_set():
            break
        if connected and time.monotonic() - started > STABLE_SESSION_S:
            backoff = 1.0
        log.info("reconnecting to G-Earth in %.0fs", backoff)
        stop.wait(backoff)
        backoff = min(backoff * 2, BACKOFF_MAX_S)
    log.info("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
