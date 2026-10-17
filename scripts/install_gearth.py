"""Register the bot in G-Earth's Extensions list (macOS, Windows, Linux).

Writes ``<G-Earth>/Extensions/Bobba Buddy_<version>/command.txt``, the same JSON-argv
format G-Earth uses for store-installed extensions. G-Earth substitutes ``{cookie}``,
``{port}`` and ``{filename}`` when it launches the extension.

Where ``<G-Earth>`` is depends on the OS:

* **macOS:** the app keeps its data in ``~/Library/Application Support/G-Earth``, which
  is the default.
* **Windows and Linux:** ``Extensions/`` sits next to ``G-Earth.jar``, wherever G-Earth
  was unzipped, so pass that folder (or the jar itself) with ``--gearth-dir``.

Usage::

    uv run python scripts/install_gearth.py            # install / update (macOS)
    uv run python scripts/install_gearth.py --gearth-dir "C:/Tools/G-Earth"  # Windows
    uv run python scripts/install_gearth.py --dry-run  # show what would be written
    uv run python scripts/install_gearth.py --uninstall

Attributes:
    PROJECT (Path): The project folder (parent of ``scripts/``).
    MAC_GEARTH_DIR (Path): G-Earth's data folder on macOS.
    FOLDER_PREFIX (str): Name prefix of the extension folder; older versions with this
        prefix are replaced.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from bobba_buddy import __version__  # noqa: E402

MAC_GEARTH_DIR = Path("~/Library/Application Support/G-Earth").expanduser()
FOLDER_PREFIX = "Bobba Buddy_"


def default_gearth_dir(platform: str = sys.platform) -> Path | None:
    """G-Earth's data folder when it can be known without asking.

    G-Earth's macOS launcher sets ``-Dgearth.data.dir`` to Application Support; on
    other systems the data folder is wherever ``G-Earth.jar`` is.

    Args:
        platform: A ``sys.platform`` value.

    Returns:
        The macOS folder, or ``None`` on Windows and Linux.
    """
    return MAC_GEARTH_DIR if platform == "darwin" else None


def resolve_gearth_dir(given: Path) -> Path:
    """Turn a ``--gearth-dir`` value into G-Earth's data folder.

    Accepts the folder itself or the path to ``G-Earth.jar`` inside it.

    Args:
        given: The path the user passed.

    Returns:
        The folder that holds (or will hold) ``Extensions/``.
    """
    path = given.expanduser()
    return path.parent if path.suffix.lower() == ".jar" else path


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
        help="G-Earth's folder (the one with G-Earth.jar) or the jar itself; "
        "required on Windows and Linux",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    gearth_dir = resolve_gearth_dir(args.gearth_dir) if args.gearth_dir else default_gearth_dir()
    if gearth_dir is None:
        print(
            "Pass --gearth-dir: the folder where G-Earth.jar is (where you unzipped "
            'G-Earth), e.g. --gearth-dir "C:\\Tools\\G-Earth" or ~/G-Earth',
            file=sys.stderr,
        )
        return 1
    extensions = gearth_dir / "Extensions"
    if not extensions.is_dir():
        print(
            f"G-Earth Extensions folder not found: {extensions}\n"
            "Check --gearth-dir, or start G-Earth once so it creates the folder.",
            file=sys.stderr,
        )
        return 1

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
        return 0

    for folder in old:
        if folder != target:
            shutil.rmtree(folder)
    (target / "extension").mkdir(parents=True, exist_ok=True)
    (target / "command.txt").write_text(content, encoding="utf-8")
    print("Done. Restart G-Earth; 'Bobba Buddy' appears in the Extensions tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
