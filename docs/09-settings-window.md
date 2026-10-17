# Settings window

[← Contents](MAIN_CONTENT.md)

Since 1.3.0, every option in `config.toml` can be changed from a Habbo-style window instead of editing the file. It works on macOS, Windows and Linux.

![The settings window on the Commands page: yellow header with the logo and the BOT ON/OFF switch, the page list on the left, and boxes of switches, text boxes and −/+ numbers](images/settings-commands.png)

## Opening it

- **From G-Earth:** press **▶** next to Bobba Buddy in the Extensions tab. Pressing ▶ again while the window is open brings it to the front instead of opening a second one.
- **Without G-Earth** (to look around or edit `config.toml`): run `uv run python -m bobba_buddy.settings`. Add `--page Avatar` to open on a page. The ON/OFF switch and status line are simulated in this mode, but Save writes the real file.

Closing the window doesn't stop the bot. It keeps running in G-Earth.

## The header

- **BOT ON / OFF** pauses everything: chat commands, copying, autopilot and anti-idle. It used to be G-Earth's ▶ button (before 1.3.0). Switching ON also clears a "bot paused" API error once you've fixed the key or model.
- **Status line:** who you're connected as (or "Waiting for the hotel"), and today's spend against the daily budget. If the bot paused itself after an API error, the reason shows here in red.

## The pages

| Page | What's on it |
|---|---|
| **Whitelist** | The Habbo names allowed to use the commands, separated by commas. You are always allowed. |
| **Commands** | One box per command (`/q`, `/roast`, `/suggest`, `/joke`): on/off, prefix, cooldown, answer length, most bubbles, and web search where it applies. `/roast` adds the same-target cooldown and the "never roast" names. |
| **Avatar** | Autopilot (waits, walk radius, waving, Habbo Club dances and expressions), how often each action is picked, copying nearby players, anti-idle and its wake-up actions, and timing. |
| **Room** | Rejoining the last room after a restart, and going back after landing on the hotel view. |
| **Claude** | Model, thinking effort, max tokens, timeout, retries, searches per `/q`, and `/q` memory. |
| **Budget** | Daily budget and question length; the number of Claude calls at once and the queue size (these two apply after the extension restarts). |
| **Advanced** | Chat timing and bubble style, packet safety (`require_seen_packets`), the prices used for the budget, and the room reload packet. |
| **About** | The logo, version, GitHub and docs links, and the fan-project disclaimer. |

![Whitelist page](images/settings-whitelist.png)

![Avatar page: autopilot switches, waits and walk radius](images/settings-avatar.png)

![Room page: rejoin and return settings](images/settings-room.png)

![Claude page: model, thinking effort and limits](images/settings-claude.png)

![Budget page: daily budget and load settings](images/settings-budget.png)

![Advanced page: chat timing, bubble style and packet safety](images/settings-advanced.png)

![About page: logo, version, GitHub and docs buttons](images/settings-about.png)

## Controls

| Control | How it works |
|---|---|
| **ON / OFF** | Click either half. |
| **− / +** | Click, or hold to keep counting. You can also type a number and press Return, or use the ↑ and ↓ keys. Values stay within the allowed range. |
| **Text box** | Prefixes, the model and the reload packet. |
| **Names** | Comma-separated, e.g. `Friend1, Friend2`. Capitals are kept in the file; matching ignores them. |
| **Choice** | Thinking effort: click one of `low` … `max`. |
| **Chips** | Wake-up actions: click to turn each one on or off. They're tried in the order shown. |

## Saving

- **Changes wait for Save.** The line next to the buttons counts unsaved changes. Changing a setting back to its saved value removes it from the count.
- **Save** (or ⌘S on a Mac, Ctrl+S elsewhere) checks everything first, with the same rules the bot uses when it loads `config.toml`. Then it writes the file and the bot applies it straight away, with no 2-second wait. "Claude calls at once" and "Queue size" still need an extension restart, and the window says so.
- **Your file stays yours.** Only the settings you changed are rewritten. Comments, order and everything else stay as they are. If `config.toml` doesn't exist yet, it's created from `config.example.toml`.
- **A bad value** (for example two commands with the same prefix, or a longest autopilot wait shorter than the shortest) isn't saved. The window jumps to that setting, turns its name red and explains the problem. The file isn't touched.
- **Revert** puts back the saved values.
- **Closing with unsaved changes** asks whether to save them first.
- **Editing the file by hand** still works. An open window reloads it when it changes, unless you have unsaved changes; then it warns you, and Save only writes the settings you changed.

## Good to know

- **On macOS** the window floats above other apps until you click into it, because a background process (the bot, started by G-Earth) isn't always allowed to take focus. While it's open it shows the Bobba Buddy logo in the Dock; the icon goes away when you close it.
- **On Linux** the window needs Tkinter: `sudo apt install python3-tk` on Debian and Ubuntu. Without it, or without a display, pressing ▶ prints "Settings window unavailable" in the G-Earth console and the bot keeps running. The python.org installers and uv's own Python builds already include Tkinter.
- **How it's built:** see [How the bot works § Settings window](01-how-the-bot-works.md#settings-window) for the threading, and the `settings/` package in the [Codebase guide](02-codebase-guide.md#layout).
