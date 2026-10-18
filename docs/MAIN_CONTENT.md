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
| 9 | [Settings window](09-settings-window.md) | A tour of the window G-Earth's ▶ button opens, with screenshots of every page; saving, errors, the live Extension Logs, and platform notes |

**API reference (Sphinx).** `docs/sphinx/` builds an HTML site that combines these guides with a reference generated from the code's Google-style docstrings. To build it:

```bash
uv run --group docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html
```

Then open `docs/sphinx/_build/html/index.html`. The published copy is at https://aaimang.github.io/bobba-buddy/. Details are in `docs/sphinx/README.md`.

## Quick links

- **I want to…**
  - change a setting → [Settings window](09-settings-window.md), or [Codebase guide § Configuration](02-codebase-guide.md#configuration) for the file itself
  - change how the bot talks → [Codebase guide § Prompts](02-codebase-guide.md#prompts-and-tone)
  - send a packet by hand → [Reading packets § G-Earth expressions](05-reading-packets.md#g-earth-packet-expressions)
  - add a new avatar action → [Codebase guide § Adding an avatar action](02-codebase-guide.md#adding-an-avatar-action)
  - update to the latest version → [Settings window § Updates](09-settings-window.md#updates)
  - see what the bot is doing right now → [Settings window § Extension Logs](09-settings-window.md#extension-logs)
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
| `v1.6.0` | **In-hotel windows, only for you:** `/bb help` and `/bb status` open Habbo popups (bold headings, problems in red, a docs button), and small notification bubbles with the Bobba Buddy logo show when settings are saved or have an error, a command hits a cooldown, something goes wrong, or you need to re-enter the room. The `/bb` line is blocked so the room never sees it. New `[notify]` section (also under Advanced) to turn them off or pick which ones. Needs G-Earth's Developer mode. New module `hotelui.py`. |
| `v1.5.1` | Security: **Update now** installs exactly the release's git tag instead of the newest commit on `main`. It checks the tag looks like `vX.Y.Z`, is on `main` on GitHub and contains the expected version before changing anything. The Claude API key is no longer passed to git, uv or the installer. |
| `v1.5.0` | **Updates:** the bot asks GitHub once a day for a newer release (`[updates] check`, on by default; also under Advanced). A banner under the settings window's header offers **Update now** (runs `git pull`, `uv sync` if needed and the installer, then asks you to restart G-Earth), **View release** and ✕; the G-Earth console says so once per version. It only updates automatically on a clean `main` checkout and never across a major version; otherwise it explains why and shows the manual steps. About gets an Updates box with Check now. New modules `updates.py`, `updater.py`, `settings/updateview.py`. |
| `v1.4.1` | Fix (macOS): reopening the settings window showed Python's icon in the Dock instead of the Bobba Buddy logo. The logo is now set again each time the window opens. |
| `v1.4.0` | **Extension Logs** page in the settings window: the bot's log live in a terminal view (last 2,000 lines kept in memory) with coloured levels, level chips, a text filter, Auto-scroll (on by default, pauses when you scroll up), a Debug switch (DEBUG logging until restart), Copy, Clear and Open log file. New DEBUG lines trace each command through queueing, the Claude call, the reply and each bubble sent. Save and Revert are hidden on About and Extension Logs; the log page's status bar wraps to two rows on a narrow window. The window shows as "Bobba Buddy" instead of "Python" in the macOS menu bar and Dock, and with the logo on the Windows taskbar. Fix: closing the window could leave it half-open when another widget had a timer pending. |
| `v1.3.3` | Fix: G-Earth's window stopped opening again because Finder recreated `.DS_Store` in the `Extensions` folder. On macOS `install_gearth.py` now leaves an empty `.DS_Store` folder there, which Finder can't overwrite and G-Earth skips. |
| `v1.3.2` | Settings window icons, as in the design mockup: one per sidebar page and box header, plus GitHub marks on the About button and footer link ([Tabler](https://tabler.io/icons) icons, MIT, as PNGs in `assets/icons/`, made by `scripts/make_icons.py`). Logo and icon loading share one tolerant loader. New screenshots in the README and the settings guide. |
| `v1.3.1` | Fix: the settings window could fail to open on Windows when Tk couldn't create the logo image (an empty `TclError`, seen in CI). The logo is now loaded from memory with one retry, and the window opens without it if it still fails. The docs build installs Tkinter on Ubuntu. |
| `v1.3.0` | **Settings window:** G-Earth's ▶ button opens a Habbo-style Tkinter window (macOS, Windows, Linux) to edit every `config.toml` option, with the bot's ON/OFF switch, live status, and an About page with the logo, version and GitHub link. Saves keep your comments (new dependency `tomlkit`) and apply at once. ▶ no longer toggles the bot; the switch is in the window. `config.toml` is unchanged. |
| `v1.2.3` | Fix: `install_gearth.py` removes a stray macOS `.DS_Store` from G-Earth's `Extensions` folder (and from its own extension folder). G-Earth 1.5.4 tried to start it as an extension and crashed before its window opened. `--dry-run` only reports it. |
| `v1.2.2` | New tagline: "Bobba Buddy: a hotel sidekick for G-Earth" (README and docs home page). |
| `v1.2.1` | `install_gearth.py` finds G-Earth by itself on Windows and Linux: from the running G-Earth, else by a quick search of Downloads, Desktop, Documents and home. `--gearth-dir` is now only a fallback. |
| `v1.2.0` | Runs on **macOS, Windows and Linux**. `install_gearth.py` takes `--gearth-dir` (G-Earth's folder or `G-Earth.jar`) on Windows and Linux and uses the right virtualenv Python per OS. State and dump files are always UTF-8. A test workflow runs lint and the tests on all three systems. README has Windows (PowerShell) setup. |
| `1.1.2` (not tagged) | Docs published to GitHub Pages (https://aaimang.github.io/bobba-buddy/) by a GitHub Actions workflow, `.github/workflows/docs.yml`, on every push to `main`. |
| `1.1.1` (not tagged) | Sphinx docs switched from Furo to the [Shibuya](https://shibuya.lepture.com) theme with a Habbo-yellow accent. Logo (a Habbo avatar made with the HabboQuests imager) in the README and docs, and an unofficial-fan-project disclaimer in the README and on every docs page. |
| `v1.1.0` | Renamed to **Bobba Buddy**: package `bobba_buddy` (run with `python -m bobba_buddy`), class `BobbaBuddy`, G-Earth title "Bobba Buddy", log file `logs/bobba-buddy.log`, project folder `bobba-buddy`. `config.toml` is unchanged. |
| `v1.0.0` | First release. Chat commands `/q`, `/roast`, `/suggest`, `/joke`, with whitelist, cooldowns, a daily budget and per-user `/q` memory. Avatar copying, autopilot and anti-idle. Room rejoin and return. Live-client packet fixes, the cost report, the G-Earth installer, these docs and the Sphinx API reference. |
