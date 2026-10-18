# How the bot works

[← Contents](MAIN_CONTENT.md)

## The big picture

The bot is a **G-Earth extension**. G-Earth is a packet proxy that sits between the Habbo AIR client and Habbo's servers. It hands every packet to its extensions before passing it on, and extensions can also send their own packets.

```
Habbo AIR client  ⇄  G-Earth (proxy)  ⇄  Habbo server
                         ⇅ local socket (port 9092)
                    this extension (Python, G-Python 0.1.6)
```

The bot plays the logged-in account, called **the owner** throughout the code. It reads chat and room packets, and sends chat bubbles and avatar actions as if the owner had typed or clicked them.

It does four jobs:

1. **Chat commands.** Whitelisted users type `/q`, `/roast`, `/suggest` or `/joke`, and the bot answers through Claude.
2. **Copying.** It mirrors what players standing next to the owner do.
3. **Autopilot.** Every 10–90 seconds (configurable) it makes the owner walk, sit, dance, wave, hold a sign or look around.
4. **Room keeping.** It blocks idling, re-enters the last room after a restart, and goes back if the owner lands on the hotel view.

## The life of a chat command

Example: a friend types `/q what's the capital of Malaysia?` in room chat.

1. **Packet in.** G-Earth delivers the incoming `Chat` packet to `BobbaBuddy._on_incoming_chat` (`bot.py`). That callback only parses and hands the work off. See [Threads](#threads-and-the-one-rule-that-matters).
2. **Is it a command?** `parse_command` (`commands.py`) matches the text against the enabled prefixes, case-insensitively, and requires a space or the end of the text after the prefix. Ordinary chat stops here.
3. **Who said it?** The room index in the packet is looked up in `RoomState` (`room.py`) to get a name. The owner's own messages come back as an echo and are ignored here; they're handled from the outgoing packet instead.
4. **Gate** (`BobbaBuddy.handle`), cheapest check first:
   - whitelist (the owner is always allowed)
   - can the reply mode be sent at all ([packet safety](06-gearth-requirements.md#packet-safety-and-developer-mode))
   - input length
   - `/roast` target lookup and opt-out
   - per-user and per-target cooldown, one request in flight per user (`access.py`)
   - daily budget (`budget.py`)
   - queue size
5. **Claude call** on a worker thread (`BobbaBuddy._work` → `ClaudeResponder.respond` in `llm.py`):
   - system prompt = base persona + the command's task (`prompts.py`)
   - user message = `[from Name | date] question`
   - `/q` adds the asker's recent history (`memory.py`) and the web search tool
6. **Format.** `sanitize` strips markdown, links, newlines and any leading `/` or `:`. `split_bubbles` cuts the text into bubbles of at most 100 UTF-16 units, at word boundaries (`textfmt.py`).
7. **Send.** The `Outbox` thread (`outbox.py`) sends one bubble every `send_interval_s`, in the mode the question came in (chat, shout or whisper). It pauses while Habbo's flood mute is active.
8. **Record.** Cost goes into `state/budget.json`, a log line goes into `logs/bobba-buddy.log`, and the Q&A pair goes into memory for `/q`.

## The avatar worker

`AvatarController` (`avatar.py`) runs on its own thread and sends at most one action every `action_gap_s` (1.5 s). Each tick it picks, in priority order:

1. **Queued copies and wake-ups.**
   - Copies come from incoming `Dance`, `Expression` and `UserUpdate` packets of players within `mimic_radius` tiles.
   - Wake-ups come from an incoming `Sleep` for the owner.
   - A queued action older than 5 s is dropped as stale.
2. **Autopilot.** When its random timer is due and it isn't paused:
   - it picks one action, weighted by the `*_weight` settings
   - after an autopilot walk it queues a wave (`wave_on_walk`)
   - it pauses for 60 s after the owner acts by hand, and 30 s after a copy
3. **Final checks** before sending:
   - skip actions the current state already satisfies (`_still_useful`)
   - with `require_seen_packets = true`, skip packet types G-Earth hasn't seen yet

**Anti-idle** works differently: it's a check on the owner's outgoing `AvatarExpression` packets. Value 5 is the client reporting "I'm idle", and the bot sets `is_blocked = True` on it so it never reaches Habbo.

## Room keeping

| Event | What the bot does |
|---|---|
| `RoomReady` (entered a room) | Clears room state, saves the room id to `state/last_room.json` |
| `CloseConnection` (left a room) | Clears room state; with `return_on_exit`, starts a timer to go back |
| Timer fires and still no room | Sends `GetGuestRoom(room_id, 0, 1)`, the navigator's "go to room" request. Retries at most `return_max_attempts` times |
| Bot (re)connects | With `rejoin_on_start`, after `rejoin_delay_s` sends the same request if no room has loaded |

The bot can't ask the server "which room am I in?" or "resend this room": the current client has no such packets (`GetHeightMap` was removed). That's why it remembers rooms itself.

## Threads and the one rule that matters

G-Python runs **every packet callback on a single thread**, and G-Earth **holds each packet** until the callback returns. So:

- **A slow callback freezes the game client.**
- **An uncaught exception kills that thread,** and packets stop flowing for good.

**The rule:** callbacks only parse and queue. Every callback is wrapped in `guarded` (`guard.py`), which catches everything.

| Thread | Started by | Does |
|---|---|---|
| G-Python reader | `Extension.start()` | Reads G-Earth's socket |
| G-Python packet thread | `Extension.start()` | Runs every intercept callback, one by one |
| main thread | `__main__.run_session()` | Waits for the session to end; opens the settings window when ▶ is pressed (`SettingsHost.serve`) |
| G-Python event threads | each event | `connection_start`, `connection_end`, `double_click` (▶) callbacks |
| `buddy-worker-N` | `ThreadPoolExecutor` (`workers = 2`) | Claude calls |
| `buddy-outbox` | `Outbox.start()` | Paced chat bubbles |
| `buddy-avatar` | `AvatarController.start()` | Copies, autopilot, wake-ups |
| timers | `threading.Timer` | Room rejoin and return |
| `buddy-update` | `__main__.main()` | Asks GitHub for the latest release at most once a day (`updates.UpdateChecker`) |
| `buddy-updater` | the settings window's **Update now** | Runs `git pull`, `uv sync` and the installer (`updater.Updater`), then exits |

Shared state (`RoomState`, `AccessControl`, `ConversationMemory`, `Budget`) is protected by locks.

## Startup and shutdown

**Starting:** `uv run python -m bobba_buddy -p 9092` runs `__main__.main()`:

1. Parse arguments. G-Earth passes `-p port -c cookie -f filename` when it launches an installed extension.
2. Set up logging (`logs/bobba-buddy.log`, rotating) and the bad-packet dump folder.
3. Load `.env` (API key) and `config.toml`. If either is missing, exit with code 2.
4. **Supervisor loop:** create an `Extension` and an `BobbaBuddy`, register intercepts, connect.
   - **Started from a terminal:** if G-Earth isn't running or closes, reconnect with backoff (1 s, 2 s, 4 s … up to 30 s).
   - **Started by G-Earth** (`-c` given): exit when G-Earth disconnects, since G-Earth manages it.
5. **On `connection_start`:** reset state, report missing packet names, send `InfoRetrieve` (asks the server for the owner's identity), print the ready notice, and start the rejoin timer.

**Stopping:** Ctrl-C (SIGINT) or SIGTERM sets a stop flag. The outbox, avatar worker and timers stop, and the extension disconnects cleanly.

## Config reloading

`ConfigStore` checks `config.toml`'s modification time at most every 2 s. A valid edit replaces the config and prints "Config reloaded". An invalid edit prints the error in red and keeps the old config. `workers` and `max_pending` are only read at startup.

## Settings window

G-Earth's ▶ button sends `double_click`. G-Python raises it on a thread of its own, but Tk has to run on the process's main thread (macOS insists), so `BobbaBuddy.open_settings()` only queues a request. The main thread, which otherwise just waits for the session to end, picks it up in `SettingsHost.serve()` and runs the window there until it closes. The bot's other threads keep working meanwhile.

- **Saving:** `settings/configfile.py` edits `config.toml` with `tomlkit`, so comments and untouched keys stay as they are. The result is checked with `parse_config()` first and written atomically, then `ConfigStore.reload_now()` applies it without waiting for the 2 s check.
- **ON/OFF:** the header switch calls `BobbaBuddy.set_enabled()`. Switching on also clears a fatal API error.
- **Name and icon:** before the first `tk.Tk()`, `theme.name_app()` sets `CFBundleName` in the running Python's in-memory bundle info on macOS (menu bar and Dock read it at app start-up), and an AppUserModelID on Windows (so the taskbar shows the window's logo, not `python.exe`'s).
- **To the front:** the bot is a background process, so on macOS the window activates the app through the Objective-C runtime and floats on top until it's focused.
- **No Tkinter** (some Linux installs) or no display: the bot keeps running and prints a notice in the G-Earth console.
- **Updates:** `updates.UpdateChecker` keeps the latest release in `state/update_check.json`; the window reads it every second and never waits on the network. **Update now** runs `updater.Updater` on its own thread while the bot keeps running the old code in memory; the new version starts when G-Earth starts the extension again.
- **Extension Logs:** at startup `logbuffer.install()` adds a logging handler that keeps the last 2,000 records in memory. `emit()` only appends under a lock, so logging from the packet thread stays cheap. The open window polls it every 200 ms on the Tk thread. The Debug button sets the root logger to DEBUG until the bot restarts.

