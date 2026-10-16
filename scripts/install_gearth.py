"""Register the bot in G-Earth's Extensions list (macOS).

Writes ``<G-Earth>/Extensions/Ask Claude_<version>/command.txt``, the same JSON-argv
format G-Earth uses for store-installed extensions. G-Earth substitutes ``{cookie}``,
``{port}`` and ``{filename}`` when it launches the extension.

Usage::

    uv run python scripts/install_gearth.py            # install / update
    uv run python scripts/install_gearth.py --dry-run  # show what would be written
    uv run python scripts/install_gearth.py --uninstall

Attributes:
    PROJECT (Path): The project folder (parent of ``scripts/``).
    DEFAULT_GEARTH_DIR (Path): G-Earth's data folder on macOS.
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

from habbo_ask_bot import __version__  # noqa: E402

DEFAULT_GEARTH_DIR = Path("~/Library/Application Support/G-Earth").expanduser()
FOLDER_PREFIX = "Ask Claude_"


def main() -> int:
    """Install, update or uninstall the G-Earth extension entry.

    Returns:
        The exit code, 0 on success or 1 if G-Earth's folder or the virtualenv is
        missing.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gearth-dir", type=Path, default=DEFAULT_GEARTH_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args()

    extensions = args.gearth_dir.expanduser() / "Extensions"
    if not extensions.is_dir():
        print(f"G-Earth Extensions folder not found: {extensions}", file=sys.stderr)
        return 1

    old = sorted(p for p in extensions.iterdir() if p.is_dir() and p.name.startswith(FOLDER_PREFIX))
    target = extensions / f"{FOLDER_PREFIX}{__version__}"

    if args.uninstall:
        for folder in old:
            print(f"{'would remove' if args.dry_run else 'removing'} {folder}")
            if not args.dry_run:
                shutil.rmtree(folder)
        return 0

    python = PROJECT / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"virtualenv python not found at {python}; run `uv sync` first", file=sys.stderr)
        return 1
    for name in (".env", "config.toml"):
        if not (PROJECT / name).exists():
            print(f"warning: {PROJECT / name} is missing; the bot won't start without it")

    command = [
        str(python),
        "-m",
        "habbo_ask_bot",
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
    (target / "command.txt").write_text(content)
    print("Done. Restart G-Earth; 'Ask Claude' appears in the Extensions tab.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
