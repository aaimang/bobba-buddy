"""One-click update: the release's git tag, ``uv sync`` and the G-Earth installer, safely.

Runs on a background thread while the bot keeps running the old code in memory;
the new version starts when G-Earth starts the extension again. It installs the
commit of the release's tag (``v1.5.1``), not whatever is newest on ``main``, and
only when that's safe: a git checkout on ``main`` that tracks ``origin/main``, no
edited tracked files, a tag that is on ``origin/main`` and has the expected
version, a fast-forward, and not a new MAJOR version. Otherwise it says why and
the window shows the manual steps. ``config.toml``, ``.env``, ``logs/`` and
``state/`` are git-ignored, so an update never touches them.

Attributes:
    MANUAL_STEPS (tuple[str, ...]): The update done by hand.
    TAG_PATTERN (re.Pattern[str]): What a release tag must look like; the tag comes
        from GitHub, so anything else (like ``--option``) is refused.
    SECRET_PREFIXES (tuple[str, ...]): Environment variables that start with one of
        these (the Claude API key) aren't passed to git, uv or the installer.
    TOOL_DIRS (tuple[str, ...]): Extra places to look for ``git`` and ``uv``; apps
        started by G-Earth don't get the shell's ``PATH``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from .updates import parse_version

log = logging.getLogger(__name__)

MANUAL_STEPS = (
    "git pull",
    "uv sync",
    "uv run python scripts/install_gearth.py, then restart G-Earth",
)
TOOL_DIRS = (
    "~/.local/bin",
    "~/.cargo/bin",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/usr/bin",
)
TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")
SECRET_PREFIXES = ("ANTHROPIC_",)

Runner = Callable[[list[str], Path, float], tuple[int, str]]


def run_command(command: list[str], cwd: Path, timeout: float) -> tuple[int, str]:
    """Run a command (no shell) and return its exit code and output.

    Args:
        command: The program and its arguments.
        cwd: Folder to run it in.
        timeout: Seconds before giving up.

    Returns:
        The exit code (``-1`` if it couldn't run) and its combined output.
    """
    try:
        done = subprocess.run(  # noqa: S603 - fixed commands, no shell
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=_clean_env(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, str(exc)
    return done.returncode, (done.stdout + done.stderr).rstrip()  # keep git's leading spaces


def _clean_env() -> dict[str, str]:
    """The environment for git and uv: no password prompts, no outside virtualenv, no API key."""
    env = {
        k: v
        for k, v in os.environ.items()
        if k != "VIRTUAL_ENV" and not k.upper().startswith(SECRET_PREFIXES)
    }
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def find_tool(name: str, extra_dirs: tuple[str, ...] = TOOL_DIRS) -> str | None:
    """Find ``git`` or ``uv``: on ``PATH``, then in the usual install folders.

    Also looks next to the Python running the bot (where ``pip install uv`` puts
    it, e.g. ``/Library/Frameworks/Python.framework/Versions/3.13/bin``).

    Args:
        name: The program, without ``.exe``.
        extra_dirs: Folders to try after ``PATH``.

    Returns:
        The full path, or ``None``.
    """
    found = shutil.which(name)
    if found:
        return found
    exe = f"{name}.exe" if sys.platform == "win32" else name
    base = Path(sys.base_prefix)
    folders = [Path(d).expanduser() for d in extra_dirs]
    folders += [base / "bin", base / "Scripts", Path(sys.executable).parent]
    if sys.platform == "win32":
        folders.append(Path(os.environ.get("USERPROFILE", "~")).expanduser() / ".local" / "bin")
    for folder in folders:
        candidate = folder / exe
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


VERSION_FILE = "src/bobba_buddy/__init__.py"


def version_in(text: str) -> str | None:
    """The ``__version__`` in the text of ``__init__.py``.

    Args:
        text: The file's contents.

    Returns:
        The version, or ``None`` if there isn't one.
    """
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else None


def read_version(project_dir: Path) -> str | None:
    """The version in a checkout's ``src/bobba_buddy/__init__.py``.

    Args:
        project_dir: The checkout.

    Returns:
        The version, or ``None`` if it can't be read.
    """
    try:
        text = (project_dir / VERSION_FILE).read_text("utf-8")
    except OSError:
        return None
    return version_in(text)


class Updater:
    """Updates one checkout to a release tag and reports how it went.

    Attributes:
        project_dir: The checkout the bot runs from.
        target: The version being updated to.
        tag: Its git tag, e.g. ``"v1.5.1"``.
        current: The running version.
        state: ``idle``, ``running``, ``done`` or ``failed``.
        step: What's happening now, for the banner.
        message: The result, or why it couldn't update.
        new_version: The version on disk after a successful update.
    """

    def __init__(
        self,
        project_dir: Path,
        target: str,
        current: str,
        *,
        tag: str | None = None,
        run: Runner = run_command,
        which: Callable[[str], str | None] = find_tool,
        python: str = sys.executable,
        installer_args: tuple[str, ...] = (),
    ) -> None:
        """Prepare an update; nothing runs until :meth:`start` or :meth:`run`.

        Args:
            project_dir: The checkout the bot runs from.
            target: The version being updated to, e.g. ``"1.5.0"``.
            current: The running version.
            tag: The release's git tag; ``v<target>`` if not given.
            run: Runs one command; injectable for tests.
            which: Finds ``git``/``uv``; injectable for tests.
            python: The virtualenv's Python, for the installer.
            installer_args: Extra installer arguments (``--dry-run`` in checks).
        """
        self.project_dir = project_dir
        self.target = target
        self.current = current
        self.tag = tag or f"v{target}"
        self._run = run
        self._which = which
        self._python = python
        self._installer_args = installer_args
        self._lock = threading.Lock()
        self.state = "idle"
        self.step = ""
        self.message = ""
        self.new_version: str | None = None

    # --- helpers -----------------------------------------------------------------

    def _set(self, *, state: str | None = None, step: str | None = None, message: str = "") -> None:
        """Update what the window shows."""
        with self._lock:
            if state is not None:
                self.state = state
            if step is not None:
                self.step = step
            if message:
                self.message = message

    def _cmd(self, command: list[str], timeout: float = 120.0) -> tuple[int, str]:
        """Run one command, logging it and its output."""
        shown = " ".join([Path(command[0]).name, *command[1:]])
        log.info("updater: $ %s", shown)
        code, output = self._run(command, self.project_dir, timeout)
        for line in output.splitlines()[-40:]:
            log.info("updater:   %s", line)
        if code != 0:
            log.warning("updater: %s exited with %d", shown, code)
        return code, output

    # --- checks ------------------------------------------------------------------

    def preflight(self) -> tuple[str | None, str, str]:
        """Check an automatic update is safe.

        Returns:
            ``(reason, git, uv)``: ``reason`` is ``None`` when it's safe, else why
            not; ``git`` and ``uv`` are the tools' paths.
        """
        try:
            if parse_version(self.target)[0] > parse_version(self.current)[0]:
                return (
                    f"{self.target} is a major version and may change config.toml; "
                    "read the release notes and update by hand",
                    "",
                    "",
                )
        except ValueError:
            return f"can't compare versions {self.current} and {self.target}", "", ""
        if not TAG_PATTERN.fullmatch(self.tag):
            return f"the release tag {self.tag!r} doesn't look like vX.Y.Z", "", ""
        git = self._which("git")
        if not git:
            return "git wasn't found on this computer", "", ""
        code, out = self._cmd([git, "rev-parse", "--is-inside-work-tree"])
        if code != 0 or out.strip() != "true":
            return f"{self.project_dir} isn't a git checkout", git, ""
        code, branch = self._cmd([git, "rev-parse", "--abbrev-ref", "HEAD"])
        branch = branch.strip()
        if code != 0 or branch != "main":
            return (
                f"this is a development checkout (branch {branch or '?'}); "
                "update it with git yourself",
                git,
                "",
            )
        code, upstream = self._cmd(
            [git, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]
        )
        if code != 0 or upstream.strip() != "origin/main":
            return "main doesn't track origin/main", git, ""
        code, dirty = self._cmd([git, "status", "--porcelain", "--untracked-files=no"])
        if code != 0:
            return "git status failed", git, ""
        changed = [line for line in dirty.splitlines() if line.strip()]
        if changed:
            # Porcelain lines are "XY path": two status letters, a space, the path.
            first = changed[0][3:].strip()
            return f"{first} has local changes", git, ""
        uv = self._which("uv")
        if not uv:
            return "uv wasn't found on this computer", git, ""
        return None, git, uv

    # --- the update --------------------------------------------------------------

    def run(self) -> bool:
        """Do the whole update (blocking; :meth:`start` runs it on a thread).

        Returns:
            ``True`` if it worked.
        """
        self._set(state="running", step="checking it's safe to update")
        reason, git, uv = self.preflight()
        if reason:
            return self._fail(f"Couldn't update: {reason}.")

        self._set(step="step 1 of 3: downloading (git)")
        code, out = self._cmd([git, "fetch", "--tags", "origin"], timeout=180)
        if code != 0:
            return self._fail("Couldn't update: git fetch failed. Check your internet connection.")
        code, commit = self._cmd(
            [git, "rev-parse", "--verify", "--quiet", f"refs/tags/{self.tag}^{{commit}}"]
        )
        commit = commit.strip()
        if code != 0 or not commit:
            return self._fail(f"Couldn't update: the release tag {self.tag} isn't on GitHub yet.")
        code, _ = self._cmd([git, "merge-base", "--is-ancestor", commit, "origin/main"])
        if code != 0:
            return self._fail(
                f"Couldn't update: the release tag {self.tag} isn't on main. Update by hand."
            )
        code, old = self._cmd([git, "rev-parse", "HEAD"])
        old = old.strip()
        code, _ = self._cmd([git, "merge-base", "--is-ancestor", "HEAD", commit])
        if code != 0:
            return self._fail(
                f"Couldn't update: main has commits that aren't in {self.tag}, so it can't "
                "fast-forward. Update by hand."
            )
        code, text = self._cmd([git, "show", f"{commit}:{VERSION_FILE}"])
        tagged = version_in(text) if code == 0 else None
        if tagged != self.target:
            return self._fail(
                f"Couldn't update: {self.tag} contains version {tagged or '?'}, "
                f"expected {self.target}. Update by hand."
            )
        code, _ = self._cmd([git, "diff", "--quiet", old, commit, "--", "uv.lock"])
        lock_changed = code == 1
        code, out = self._cmd([git, "merge", "--ff-only", commit])
        if code != 0:
            return self._fail("Couldn't update: git couldn't fast-forward. Update by hand.")

        if lock_changed:
            self._set(step="step 2 of 3: installing packages (uv sync)")
            code, out = self._cmd([uv, "sync"], timeout=600)
            if code != 0:
                return self._fail(
                    "Downloaded the new version, but uv sync failed (on Windows a "
                    "package in use can't be replaced). Close G-Earth, run uv sync in "
                    f"{self.project_dir}, then start G-Earth again."
                )
        else:
            log.info("updater: uv.lock unchanged, skipping uv sync")

        self._set(step="step 3 of 3: registering with G-Earth")
        installer = str(self.project_dir / "scripts" / "install_gearth.py")
        code, out = self._cmd([self._python, installer, *self._installer_args])
        if code != 0:
            return self._fail(
                "Downloaded the new version, but registering it with G-Earth failed. "
                "Run uv run python scripts/install_gearth.py, then restart G-Earth."
            )

        self.new_version = read_version(self.project_dir) or self.target
        self._set(
            state="done",
            step="",
            message=(
                f"Updated to {self.new_version}. Restart G-Earth to start the new version. "
                f"The bot keeps running {self.current} until then."
            ),
        )
        log.info("updater: done, %s -> %s", self.current, self.new_version)
        return True

    def _fail(self, message: str) -> bool:
        """Stop with a message."""
        log.warning("updater: %s", message)
        self._set(state="failed", step="", message=message)
        return False

    def start(self) -> threading.Thread:
        """Run the update on a background thread (``buddy-updater``).

        Returns:
            The started thread.
        """
        self._set(state="running", step="starting")
        thread = threading.Thread(target=self._safe_run, name="buddy-updater", daemon=True)
        thread.start()
        return thread

    def _safe_run(self) -> None:
        """:meth:`run`, turning any surprise into a failure message."""
        try:
            self.run()
        except Exception as exc:
            log.exception("updater crashed")
            self._fail(f"Couldn't update: {exc}.")
