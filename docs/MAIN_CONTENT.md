# Bobba Buddy: documentation

Documentation for the Habbo AIR chat and avatar bot that runs as a G-Earth extension. The project README covers day-to-day use; these pages go deeper.

## Contents

| # | Topic | What's inside |
|---|---|---|
| 1 | [How the bot works](01-how-the-bot-works.md) | Features, the life of a chat command, the avatar worker, threads, startup and shutdown |
| 2 | [Codebase guide](02-codebase-guide.md) | Every module, class and script, config and state files, the tests, how to change things safely |
| 3 | [Habbo packet reference](03-packet-reference.md) | Incoming and outgoing packets the bot uses: layouts, ids for the current build, live-client quirks, profile and entity structures |
| 4 | [Actions, directions, expressions and tiles](04-actions-directions-tiles.md) | Value tables for dances, expressions, signs, postures, directions and `LookTo`; how tiles, the floor map and the door work |
| 5 | [Reading Habbo packets](05-reading-packets.md) | Wire format, G-Earth's packet logger and expressions, decoding hex by hand (with real captures), reading packets in G-Python |
| 6 | [G-Earth requirements](06-gearth-requirements.md) | G-Earth versions, packet safety and Developer mode, the extension protocol, running Python and Java extensions |
| 7 | [Bugs and fixes](07-bugs-and-fixes.md) | Every problem hit while building this bot, its cause, the workaround and the fix |
| 8 | [Sources and repositories](08-sources-and-repos.md) | The GitHub repos, files and APIs used to learn G-Python, G-Earth, G-Rust and the packet layouts |

**API reference (Sphinx).** `docs/sphinx/` builds an HTML site that combines these guides with a reference generated from the code's Google-style docstrings. To build it:

```bash
uv run --group docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html
```

Then open `docs/sphinx/_build/html/index.html`. The published copy is at https://aaimang.github.io/bobba-buddy/. Details are in `docs/sphinx/README.md`.

## Quick links

- **I want to…**
  - change a setting → [Codebase guide § Configuration](02-codebase-guide.md#configuration)
  - change how the bot talks → [Codebase guide § Prompts](02-codebase-guide.md#prompts-and-tone)
  - send a packet by hand → [Reading packets § G-Earth expressions](05-reading-packets.md#g-earth-packet-expressions)
  - add a new avatar action → [Codebase guide § Adding an avatar action](02-codebase-guide.md#adding-an-avatar-action)
- **Something broke:**
  - a file appeared in `logs/bad-packets/` → [Reading packets § Decoding a bad-packet dump](05-reading-packets.md#decoding-a-bad-packet-dump)
  - actions don't show up → [G-Earth requirements § Packet safety](06-gearth-requirements.md#packet-safety-and-developer-mode)
  - an error I haven't seen → [Bugs and fixes](07-bugs-and-fixes.md)

## Version history

The project uses [semantic versioning](https://semver.org): `MAJOR.MINOR.PATCH`, tagged in git as `vMAJOR.MINOR.PATCH`. The version lives in `pyproject.toml` and `src/bobba_buddy/__init__.py`; change both together.

- **PATCH** (`1.0.1`): bug fixes and packet-layout fixes that change no settings.
- **MINOR** (`1.1.0`): new commands, avatar actions or config options; existing `config.toml` files keep working.
- **MAJOR** (`2.0.0`): changes that break existing `config.toml` files, such as renamed sections or keys.

Each version below has a git tag (`v1.2.2` and so on), except 1.1.1 and 1.1.2: their changes are in the history but the tags were removed, so they ship as part of `v1.2.0`. Only `v1.2.2` has a GitHub Release.

| Version | Contents |
|---|---|
| `v1.3.0` | **Settings window:** G-Earth's ▶ button opens a Habbo-style Tkinter window (macOS, Windows, Linux) to edit every `config.toml` option, with the bot's ON/OFF switch, live status, and an About page with the logo, version and GitHub link. Saves keep your comments (new dependency `tomlkit`) and apply at once. ▶ no longer toggles the bot; the switch is in the window. `config.toml` is unchanged. |
| `v1.2.2` | New tagline: "Bobba Buddy: a hotel sidekick for G-Earth" (README and docs home page). |
| `v1.2.1` | `install_gearth.py` finds G-Earth by itself on Windows and Linux: from the running G-Earth, else by a quick search of Downloads, Desktop, Documents and home. `--gearth-dir` is now only a fallback. |
| `v1.2.0` | Runs on **macOS, Windows and Linux**. `install_gearth.py` takes `--gearth-dir` (G-Earth's folder or `G-Earth.jar`) on Windows and Linux and uses the right virtualenv Python per OS. State and dump files are always UTF-8. A test workflow runs lint and the tests on all three systems. README has Windows (PowerShell) setup. |
| `1.1.2` (not tagged) | Docs published to GitHub Pages (https://aaimang.github.io/bobba-buddy/) by a GitHub Actions workflow, `.github/workflows/docs.yml`, on every push to `main`. |
| `1.1.1` (not tagged) | Sphinx docs switched from Furo to the [Shibuya](https://shibuya.lepture.com) theme with a Habbo-yellow accent. Logo (a Habbo avatar made with the HabboQuests imager) in the README and docs, and an unofficial-fan-project disclaimer in the README and on every docs page. |
| `v1.1.0` | Renamed to **Bobba Buddy**: package `bobba_buddy` (run with `python -m bobba_buddy`), class `BobbaBuddy`, G-Earth title "Bobba Buddy", log file `logs/bobba-buddy.log`, project folder `bobba-buddy`. `config.toml` is unchanged. |
| `v1.0.0` | First release. Chat commands `/q`, `/roast`, `/suggest`, `/joke`, with whitelist, cooldowns, a daily budget and per-user `/q` memory. Avatar copying, autopilot and anti-idle. Room rejoin and return. Live-client packet fixes, the cost report, the G-Earth installer, these docs and the Sphinx API reference. |
