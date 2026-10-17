"""Register the bot in G-Earth's Extensions list (macOS, Windows, Linux).

Writes ``<G-Earth>/Extensions/Bobba Buddy_<version>/command.txt``, the same JSON-argv
format G-Earth uses for store-installed extensions. G-Earth substitutes ``{cookie}``,
``{port}`` and ``{filename}`` when it launches the extension.

``<G-Earth>`` is found like this (``--gearth-dir`` always wins):

* **macOS:** the app keeps its data in ``~/Library/Application Support/G-Earth``.
* **Windows and Linux:** ``Extensions/`` sits next to G-Earth's program
  (``G-Earth.exe``, ``G-Earth.jar`` or the ``G-Earth`` launcher), wherever it was
  unzipped. The script first looks for a **running G-Earth** and uses its folder, then
  **searches** the home, Downloads, Desktop and Documents folders. If it finds none, or
  more than one, it asks for ``--gearth-dir``.

Usage::

    uv run python scripts/install_gearth.py            # install / update
    uv run python scripts/install_gearth.py --gearth-dir "C:/Tools/G-Earth"
    uv run python scripts/install_gearth.py --dry-run  # show what would be written
    uv run python scripts/install_gearth.py --uninstall

Attributes:
    PROJECT (Path): The project folder (parent of ``scripts/``).
    MAC_GEARTH_DIR (Path): G-Earth's data folder on macOS.
    FOLDER_PREFIX (str): Name prefix of the extension folder; older versions with this
        prefix are replaced.
    PROGRAM_NAMES (frozenset[str]): Lowercased file names of G-Earth's program.
    SEARCH_DEPTH (int): Folder levels scanned below Downloads, Desktop and Documents.
    HOME_DEPTH (int): Folder levels scanned below the home folder itself.
    SEARCH_SECONDS (float): Time limit for the whole folder search.
    SKIP_DIRS (frozenset[str]): Folder names never scanned (large, never G-Earth).
    SKIP_SUFFIXES (tuple[str, ...]): Folder name endings never scanned (app bundles,
        photo and music libraries).
    DS_STORE (str): Finder's hidden folder-settings file, which G-Earth mistakes for an
        extension (macOS only).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from bobba_buddy import __version__  # noqa: E402

MAC_GEARTH_DIR = Path("~/Library/Application Support/G-Earth").expanduser()
FOLDER_PREFIX = "Bobba Buddy_"
DS_STORE = ".DS_Store"
PROGRAM_NAMES = frozenset({"g-earth.jar", "g-earth.exe", "g-earth"})
SEARCH_DEPTH = 3
HOME_DEPTH = 1
SEARCH_SECONDS = 10.0
SKIP_DIRS = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "site-packages",
        "AppData",
        "Library",
        "Pictures",
        "Music",
        "Movies",
        "Videos",
    }
)
SKIP_SUFFIXES = (".app", ".photoslibrary", ".musiclibrary", ".git")

# A Windows command line: "quoted paths" or bare tokens.
_TOKEN = re.compile(r'"([^"]+)"|(\S+)')


def default_gearth_dir(platform: str = sys.platform) -> Path | None:
    """G-Earth's data folder when it's fixed for the OS.

    G-Earth's macOS launcher sets ``-Dgearth.data.dir`` to Application Support; on
    other systems the data folder is wherever G-Earth's program is.

    Args:
        platform: A ``sys.platform`` value.

    Returns:
        The macOS folder, or ``None`` on Windows and Linux.
    """
    return MAC_GEARTH_DIR if platform == "darwin" else None


def resolve_gearth_dir(given: Path) -> Path:
    """Turn a ``--gearth-dir`` value into G-Earth's data folder.

    Accepts the folder itself or the path to ``G-Earth.jar`` / ``G-Earth.exe`` in it.

    Args:
        given: The path the user passed.

    Returns:
        The folder that holds (or will hold) ``Extensions/``.
    """
    path = given.expanduser()
    return path.parent if path.suffix.lower() in (".jar", ".exe") else path


def venv_python(project: Path, platform: str = sys.platform) -> Path:
    """The virtualenv's Python, in the layout uv creates on this OS.

    Args:
        project: The project folder holding ``.venv``.
        platform: A ``sys.platform`` value.

    Returns:
        ``.venv/Scripts/python.exe`` on Windows, ``.venv/bin/python`` elsewhere.
    """
    if platform == "win32":
        return project / ".venv" / "Scripts" / "python.exe"
    return project / ".venv" / "bin" / "python"


def is_gearth_program(path: str) -> bool:
    """Whether a path names G-Earth's program file.

    Args:
        path: A file path from a command line or a directory listing.

    Returns:
        ``True`` for ``G-Earth.jar``, ``G-Earth.exe`` or the ``G-Earth`` launcher,
        also as versioned jars such as ``G-Earth-1.5.4.jar``.
    """
    name = re.split(r"[\\/]", path.strip())[-1].lower()
    if name in PROGRAM_NAMES:
        return True
    return name.startswith("g-earth") and name.endswith(".jar")


def gearth_dir_for(program: Path) -> Path | None:
    """G-Earth's data folder for one of its program files.

    G-Earth creates ``Extensions/`` next to its jar on first start. Bundles keep the
    jar either beside the launcher or one level down, so both the program's folder
    and the folder above it are checked.

    Args:
        program: Path to ``G-Earth.exe``, ``G-Earth.jar`` or the ``G-Earth`` launcher.

    Returns:
        The first of those folders that has an ``Extensions/`` folder, or ``None``.
    """
    for folder in (program.parent, program.parent.parent):
        if (folder / "Extensions").is_dir():
            return folder
    return None


def programs_in_command_line(command_line: str) -> list[str]:
    """Paths to G-Earth's program in one process command line.

    Args:
        command_line: A command line as Windows reports it (quoted paths allowed).

    Returns:
        Every token that names G-Earth's program.
    """
    tokens = (quoted or bare for quoted, bare in _TOKEN.findall(command_line))
    return [token for token in tokens if is_gearth_program(token)]


def _windows_process_command_lines() -> list[str]:
    """Executable paths and command lines of G-Earth and Java processes on Windows.

    Returns:
        One string per process; empty if PowerShell isn't available.
    """
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'G-Earth*' "
        "-or $_.Name -like 'java*' } | ForEach-Object { "
        "'\"' + $_.ExecutablePath + '\" ' + $_.CommandLine }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _linux_process_arguments(proc: Path = Path("/proc")) -> list[tuple[Path | None, list[str]]]:
    """Working folder and arguments of every process, from ``/proc/<pid>``.

    Args:
        proc: The proc filesystem root; injectable for tests.

    Returns:
        ``(cwd, args)`` per readable process; ``cwd`` is ``None`` if it can't be read.
    """
    processes = []
    try:
        entries = list(proc.iterdir())
    except OSError:
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        args = [arg.decode("utf-8", "replace") for arg in raw.split(b"\0") if arg]
        if not args:
            continue
        try:
            cwd: Path | None = (entry / "cwd").resolve(strict=True)
        except OSError:
            cwd = None
        processes.append((cwd, args))
    return processes


def programs_in_arguments(args: Iterable[str], cwd: Path | None) -> list[Path]:
    """G-Earth program files named in a Linux process's arguments.

    Handles ``-jar G-Earth.jar``, classpaths joined with ``:``, and paths relative
    to the process's working folder.

    Args:
        args: The process arguments.
        cwd: The process's working folder, for relative paths.

    Returns:
        Absolute paths where possible.
    """
    found = []
    for arg in args:
        for part in arg.split(":"):
            if not is_gearth_program(part):
                continue
            path = Path(part)
            if not path.is_absolute() and cwd is not None:
                path = cwd / path
            found.append(path)
    return found


def running_gearth_programs(platform: str = sys.platform) -> list[Path]:
    """G-Earth program files of the G-Earth instances running now.

    Args:
        platform: A ``sys.platform`` value.

    Returns:
        Paths to ``G-Earth.exe`` / ``G-Earth.jar`` / ``G-Earth``; empty on macOS or
        when G-Earth isn't running.
    """
    found: list[Path] = []
    if platform == "win32":
        for line in _windows_process_command_lines():
            found.extend(Path(p) for p in programs_in_command_line(line))
    elif platform.startswith("linux"):
        for cwd, args in _linux_process_arguments():
            found.extend(programs_in_arguments(args, cwd))
    return list(dict.fromkeys(found))


def search_roots(home: Path | None = None) -> list[tuple[Path, int]]:
    """Folders searched for an unzipped G-Earth, most likely first, with their depth.

    Args:
        home: The user's home folder; defaults to ``Path.home()``.

    Returns:
        ``(folder, depth)`` pairs: Downloads, Desktop and Documents ``SEARCH_DEPTH``
        levels deep, then the home folder itself only ``HOME_DEPTH`` level deep.
    """
    home = home or Path.home()
    deep = [(home / name, SEARCH_DEPTH) for name in ("Downloads", "Desktop", "Documents")]
    return [*deep, (home, HOME_DEPTH)]


def search_gearth_programs(
    roots: Iterable[tuple[Path, int]], time_limit: float = SEARCH_SECONDS
) -> list[Path]:
    """G-Earth program files found under the given folders.

    Skips hidden folders, ``SKIP_DIRS``, ``SKIP_SUFFIXES``, and any folder that is
    itself one of the roots (so Downloads isn't walked twice). Stops early after
    ``time_limit`` seconds, keeping what it found so far.

    Args:
        roots: ``(folder, depth)`` pairs; depth is how many levels below the folder
            are scanned.
        time_limit: Seconds before the search gives up.

    Returns:
        Paths to G-Earth's program files, without duplicates.
    """
    found: dict[Path, None] = {}
    root_paths = {folder.resolve() for folder, _ in roots if folder.is_dir()}
    deadline = time.monotonic() + time_limit

    def skipped(name: str, path: Path) -> bool:
        return (
            name.startswith(".")
            or name in SKIP_DIRS
            or name.lower().endswith(SKIP_SUFFIXES)
            or path.resolve() in root_paths
        )

    def walk(folder: Path, depth: int, max_depth: int) -> None:
        if time.monotonic() > deadline:
            return
        try:
            entries = list(os.scandir(folder))
        except OSError:
            return
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if not is_dir and is_gearth_program(entry.name):
                found[Path(entry.path).resolve()] = None
            elif is_dir and depth < max_depth and not skipped(entry.name, Path(entry.path)):
                walk(Path(entry.path), depth + 1, max_depth)

    for folder, max_depth in roots:
        if folder.is_dir():
            walk(folder, 0, max_depth)
    return list(found)


def find_gearth_dirs(programs: Iterable[Path]) -> list[Path]:
    """Distinct G-Earth data folders for a set of program files.

    Args:
        programs: G-Earth program files.

    Returns:
        Each folder holding an ``Extensions/`` folder, once.
    """
    dirs: dict[Path, None] = {}
    for program in programs:
        folder = gearth_dir_for(program)
        if folder is not None:
            dirs[folder.resolve()] = None
    return list(dirs)


def locate_gearth_dir(
    given: Path | None, platform: str = sys.platform, home: Path | None = None
) -> tuple[Path | None, str]:
    """Pick G-Earth's data folder: ``--gearth-dir``, the OS default, or detection.

    Args:
        given: The ``--gearth-dir`` value, if any.
        platform: A ``sys.platform`` value.
        home: The home folder to search; defaults to ``Path.home()``.

    Returns:
        The folder (or ``None``) and a one-line message saying how it was found or
        why it wasn't.
    """
    if given is not None:
        return resolve_gearth_dir(given), "using --gearth-dir"
    fixed = default_gearth_dir(platform)
    if fixed is not None:
        return fixed, "using G-Earth's macOS data folder"

    running = find_gearth_dirs(running_gearth_programs(platform))
    if len(running) == 1:
        return running[0], "found the running G-Earth"
    if len(running) > 1:
        return None, "several G-Earths are running: " + ", ".join(map(str, running))

    found = find_gearth_dirs(search_gearth_programs(search_roots(home)))
    if len(found) == 1:
        return found[0], "found G-Earth by searching your folders"
    if len(found) > 1:
        return None, "found several G-Earth folders: " + ", ".join(map(str, found))
    return None, "couldn't find G-Earth (start it once, or keep it running while installing)"


def remove_ds_store(folder: Path, dry_run: bool = False) -> bool:
    """Remove a stray ``.DS_Store`` directly inside ``folder``.

    Finder leaves this hidden file in any folder you open on macOS. G-Earth 1.5.4 tries
    to start every entry in ``Extensions/`` as an extension; for ``.DS_Store`` it crashes
    with a ``NullPointerException`` before its window opens, so G-Earth keeps running
    with no window. Only this one file is touched; elsewhere than macOS it's a no-op.

    Args:
        folder: The folder to clean.
        dry_run: Only report what would be removed.

    Returns:
        ``True`` if there was one (removed, or would be in a dry run).
    """
    path = folder / DS_STORE
    if not path.is_file():
        return False
    print(
        f"{'would remove' if dry_run else 'removing'} {path}: G-Earth tries to start it "
        "as an extension and then never opens its window"
    )
    if not dry_run:
        try:
            path.unlink()
        except FileNotFoundError:
            pass  # already gone
        except OSError as exc:
            print(f"warning: couldn't remove {path}: {exc}", file=sys.stderr)
    return True


def main() -> int:
    """Install, update or uninstall the G-Earth extension entry.

    Returns:
        The exit code, 0 on success or 1 if G-Earth's folder or the virtualenv is
        missing.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gearth-dir",
        type=Path,
        default=None,
        help="G-Earth's folder (the one with G-Earth.exe / G-Earth.jar) or the program "
        "itself; only needed if it can't be found automatically",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    gearth_dir, how = locate_gearth_dir(args.gearth_dir)
    if gearth_dir is None:
        print(
            f"G-Earth folder not found: {how}.\n"
            "Pass --gearth-dir with the folder that has G-Earth.exe or G-Earth.jar, "
            'e.g. --gearth-dir "C:/Tools/G-Earth" or ~/G-Earth',
            file=sys.stderr,
        )
        return 1
    print(f"G-Earth folder: {gearth_dir} ({how})")
    extensions = gearth_dir / "Extensions"
    if not extensions.is_dir():
        print(
            f"G-Earth Extensions folder not found: {extensions}\n"
            "Check --gearth-dir, or start G-Earth once so it creates the folder.",
            file=sys.stderr,
        )
        return 1

    remove_ds_store(extensions, args.dry_run)
    old = sorted(p for p in extensions.iterdir() if p.is_dir() and p.name.startswith(FOLDER_PREFIX))
    target = extensions / f"{FOLDER_PREFIX}{__version__}"

    if args.uninstall:
        for folder in old:
            print(f"{'would remove' if args.dry_run else 'removing'} {folder}")
            if not args.dry_run:
                shutil.rmtree(folder)
        return 0

    python = venv_python(PROJECT)
    if not python.exists():
        print(f"virtualenv python not found at {python}; run `uv sync` first", file=sys.stderr)
        return 1
    for name in (".env", "config.toml"):
        if not (PROJECT / name).exists():
            print(f"warning: {PROJECT / name} is missing; the bot won't start without it")

    command = [
        str(python),
        "-m",
        "bobba_buddy",
        "--project-dir",
        str(PROJECT),
        "-c",
        "{cookie}",
        "-p",
        "{port}",
        "-f",
        "{filename}",
    ]
    content = json.dumps(command, separators=(",", ":"))
    print(f"{'would write' if args.dry_run else 'writing'} {target / 'command.txt'}:\n  {content}")
    if args.dry_run:
        remove_ds_store(target, dry_run=True)
        return 0

    for folder in old:
        if folder != target:
            shutil.rmtree(folder)
    (target / "extension").mkdir(parents=True, exist_ok=True)
    (target / "command.txt").write_text(content, encoding="utf-8")
    remove_ds_store(target)
    print("Done. Restart G-Earth; 'Bobba Buddy' appears in the Extensions tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
