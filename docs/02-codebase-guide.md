# Codebase guide

[← Contents](MAIN_CONTENT.md)

## Layout

```
bobba-buddy/
├─ pyproject.toml            dependencies: g-python==0.1.6, anthropic>=1.8,<2, python-dotenv, tomlkit
├─ config.example.toml       every option, commented (copy to config.toml)
├─ config.toml               your settings (git-ignored)
├─ .env / .env.example       ANTHROPIC_API_KEY (.env is git-ignored)
├─ README.md                 user guide
├─ docs/                     these pages
│  └─ sphinx/                Sphinx project: guides + API reference from docstrings
├─ src/bobba_buddy/
│  ├─ __main__.py            CLI entry point, logging, supervisor/reconnect loop
│  ├─ bot.py                 BobbaBuddy: wires packets → commands → Claude → outbox; room keeping
│  ├─ avatar.py              AvatarController: copying, autopilot, anti-idle
│  ├─ protocol.py            packet parsing and building (pure functions)
│  ├─ room.py                RoomState: users, positions, owner, floor map, seen packets
│  ├─ commands.py            command registry and parsing
│  ├─ prompts.py             system prompts and user-message building
│  ├─ llm.py                 ClaudeResponder: API call, pause_turn, error mapping
│  ├─ textfmt.py             sanitize + split into ≤100-char bubbles
│  ├─ outbox.py              paced chat sender
│  ├─ access.py              whitelist, cooldowns, in-flight tracking
│  ├─ budget.py              daily spend cap and cost estimate
│  ├─ memory.py              per-user /q history
│  ├─ config.py              typed config, validation, hot reload
│  ├─ updates.py             UpdateChecker: asks GitHub for the latest release, once a day
│  ├─ updater.py             Updater: one-click update (git pull, uv sync, installer)
│  ├─ guard.py               exception guard + bad-packet reporting
│  ├─ gpython.py             single import point for g_python (silences its warnings)
│  ├─ assets/logo.png        the logo, for the settings window (same file as the docs logo)
│  ├─ assets/icons/          Tabler icon PNGs for the settings window (MIT, LICENSE inside)
│  └─ settings/              the settings window that G-Earth's ▶ button opens
│     ├─ host.py             SettingsHost: runs the window on the main thread; BotBridge
│     ├─ window.py           SettingsWindow (Tkinter), run_window()
│     ├─ widgets.py          Habbo-style controls: Pill, Stepper, Chips, ForumButton, Box…
│     ├─ schema.py           pages, boxes and fields shown (data only)
│     ├─ configfile.py       read/write config.toml, keeping comments (tomlkit)
│     ├─ logbuffer.py        keeps the latest log lines in memory (LogBuffer handler)
│     ├─ logview.py          the Extension Logs page (LogView terminal)
│     ├─ updateview.py       the update banner and About's Updates box
│     ├─ theme.py            colours, fonts, DPI and bring-to-front helpers
│     └─ __main__.py         preview without G-Earth: python -m bobba_buddy.settings
├─ scripts/
│  ├─ smoke_claude.py        one live Claude call per command (~$0.02)
│  ├─ install_gearth.py      registers the bot in G-Earth's Extensions list (macOS, Windows, Linux)
│  ├─ cost_report.py         spend by day, command and person
│  └─ make_icons.py          renders the settings window's Tabler icons to PNG (needs PyMuPDF)
├─ tests/                    offline pytest suite (fake G-Earth, fake Claude)
├─ logs/                     bobba-buddy.log (rotating), bad-packets/ (git-ignored)
└─ state/                    budget.json, last_room.json, update_check.json (git-ignored)
```

## Modules

### `__main__.py`
- **`main()`:** arguments, logging, `.env`, config, the API key check, and the supervisor loop.
- **`run_session()`:** one G-Earth connection: builds `Extension` and `BobbaBuddy`, waits until disconnect.
- **`start_extension()`:** wraps `Extension.start()`. g-python can hang forever if G-Earth refuses the connection, so this breaks its internal barrier.

### `bot.py`: `BobbaBuddy`
- **Setup:** `install()` registers every intercept and event; `start()` / `stop()` run the outbox, avatar worker and timers.
- **Room and session handlers:** `_on_connection_start`, `_on_users`, `_on_user_remove`, `_on_room_ready`, `_on_room_closed`, `_on_user_object`, `_on_flood_control`.
- **Chat:**
  - `_on_incoming_chat` / `_on_outgoing_chat` detect commands.
  - `handle()` is the gate.
  - `_work()` runs on a worker thread.
  - `_reply()` / `_reply_mode()` / `_send_bubble()` send the answer.
- **Room keeping:** `rejoin_last_room()`, `return_to_last_room()`, `_save_last_room()`.
- **Owner messages:** `notice()` / `notice_once()` write to the G-Earth console and the log, never the room.

### `avatar.py`: `AvatarController`
- **`install()`:** intercepts `UserUpdate`, `Dance`, `Expression`, `Sleep`, `FloorHeightMap`, `RoomEntryTile`, and the owner's outgoing action packets.
- **`step()`:** one tick of the worker: queued action, or autopilot, then checks, then send.
- **`_autopilot_action()`:** weighted random pick. **`_wake_action()`:** anti-idle. **`_on_owner_action()`:** blocks the idle report and pauses autopilot when the owner acts.

### `protocol.py`
Pure functions: `parse_*` read a packet, `build_*` make one. See [Packet reference](03-packet-reference.md). Both `parse_users` and `parse_user_updates` are **tolerant**: they return `(items, problem)` and never raise, because the live client has drifted from documented layouts ([Bugs](07-bugs-and-fixes.md)).

### `room.py`: `RoomState`
Thread-safe room snapshot:
- **People:** users by room index, positions, sitting/sign/dance state per index.
- **Owner:** id, name and room index.
- **Room:** the floor tiles and the door tile.
- **Chat details:** which outgoing packet types G-Earth has seen, and the owner's chat style and tail int.

Helpers: `walk_targets()`, `free_neighbours()`, `is_player_near_owner()`, `chebyshev()`.

### `commands.py`, `prompts.py`, `llm.py`
- **`commands.py`:** `CommandSpec` per command (`q`, `roast`, `suggest`, `joke`); `parse_command()` and `usage_text()`.
- **`prompts.py`:** `BASE_SYSTEM` (persona and rules), `ADDENDA` (per-command task), `system_prompt()`, `build_user_message()`.
- **`llm.py`:** `ClaudeResponder.respond()` returns a `Reply` with a kind (`OK`, `REFUSAL`, `BUSY`, `FATAL`, `BAD_REQUEST`) and usage metrics. `extract_answer()` drops the preamble the model writes before a web search.

### Smaller modules
- **`textfmt.py`:** `sanitize()`, `split_bubbles()`, and `ulen()`, which counts UTF-16 units the way the AIR client does.
- **`outbox.py`:** `Outbox` with `enqueue`, `pause_for` (flood mute), `clear` (room change) and `wait_idle` (tests).
- **`access.py`:** `AccessControl.acquire()` returns a `Ticket` or a `Denial`; `release(refund=True)` gives cooldowns back after a failure that wasn't the user's fault.
- **`budget.py`:** `Budget` (daily total in `state/budget.json`, resets at local midnight) and `estimate_cost()`.
- **`memory.py`:** `ConversationMemory`, which keeps the last `max_turns` Q&A pairs per user and expires them after `ttl_s`.
- **`config.py`:** dataclasses per section, `parse_config()` (rejects unknown keys and wrong types), and `ConfigStore` (hot reload).
- **`guard.py`:** `guarded()`, `report_bad_packet()` (log once and save the whole packet), `configure_dump_dir()`.
- **`settings/`:** the settings window. `schema.PAGES` lists what it shows; `configfile.save_values()` validates and writes `config.toml`; `host.SettingsHost` runs the window on the main thread; `logbuffer.install()` (called at startup) feeds the Extension Logs page. See [How the bot works](01-how-the-bot-works.md#settings-window).

## Configuration

`config.toml` sections map to dataclasses in `config.py`:

| Section | Dataclass | Controls |
|---|---|---|
| top level | — | `whitelist` |
| `[claude]` | `ClaudeConfig` | model, effort, max tokens, timeout, retries, web search uses |
| `[limits]` | `LimitsConfig` | workers, queue size, daily budget, max input length |
| `[chat]` | `ChatConfig` | bubble limit, send interval, flood margin, bubble style, `require_seen_packets` |
| `[memory]` | `MemoryConfig` | `max_turns`, `ttl_s` |
| `[pricing]` | `PricingConfig` | per-token and per-search prices for the budget |
| `[room]` | `RoomConfig` | `room_reload_packet`, rejoin and return options |
| `[avatar]` | `AvatarConfig` | autopilot, copying, anti-idle, weights, HC switches |
| `[commands.<name>]` | `CommandConfig` | `enabled`, `prefix`, cooldowns, `max_chars`, `max_bubbles`, `web_search`, `opt_out` |

The roast command's settings live under `[commands.roast]`, matching its `/roast` prefix.

## State and log files

| File | Written by | Contents |
|---|---|---|
| `state/budget.json` | `Budget` | `{"date", "spent_usd", "requests"}` for today |
| `state/last_room.json` | `BobbaBuddy._save_last_room` | `{"room_id"}` of the last room entered |
| `logs/bobba-buddy.log` | logging | Everything; one `cmd=…` line per Claude call |
| `logs/bad-packets/*.hex` | `guard.report_bad_packet` | Full copy of the first unparseable packet per type per session |

## Prompts and tone

All wording is in `src/bobba_buddy/prompts.py`:
- **`BASE_SYSTEM`:** the persona (a chill Habbo regular) and the rules every reply follows: same language as the question, plain text, the Habbo Way, and ignoring prompt injection.
- **`ADDENDA["q"]`:** brief by default; longer only when needed.
- **`ADDENDA["roast"]`:** the `/roast` prompt. It rotates styles (backhanded compliment, comparison, exaggeration, rhyme, fake sympathy), with a guardrail against real-world appearance, identity, family and health.
- **`ADDENDA["suggest"]`, `ADDENDA["joke"]`:** the task wording for those commands.

`{max_chars}` in each task comes from `[commands.<name>] max_chars`. Editing `prompts.py` needs a bot restart; `max_chars` does not.

## Docstrings

Every module, class and function has a **Google-style** docstring (`Args:`, `Returns:`, `Raises:`, `Attributes:`). Sphinx's napoleon extension reads them to build the API reference in `docs/sphinx/`. `uv run ruff check .` enforces the style through ruff's `D` rules with `convention = "google"`; test functions are exempt, since their names say what they check.

## Tests

`uv run pytest` runs about 250 offline tests in under a few seconds. On GitHub, `.github/workflows/tests.yml` runs ruff and the tests on macOS, Windows and Linux on every push.

| File | Covers |
|---|---|
| `conftest.py` | `FakeExtension`, `FakeResponder`, `InlineExecutor`, packet builders (`player`, `pet`, `bot_entity`, `user_update`, `floor_map`…) |
| `test_bot.py` | End-to-end chat behaviour: modes, whitelist, cooldowns, budget, flood, roast, packet safety, rejoin/return |
| `test_avatar.py` | Copying, autopilot, anti-idle, live `UserUpdate` capture, strict vs default mode |
| `test_protocol.py` | Packet round trips, live `Users` captures, tolerant parsing |
| `test_textfmt.py`, `test_commands_prompts.py`, `test_config.py`, `test_state.py`, `test_llm.py`, `test_guard.py` | The individual modules |
| `test_settings_*.py` | The settings window: saving with comments kept, every config key shown and every icon present, the main-thread host, the Extension Logs buffer and page, and the window itself (skipped without a display) |
| `test_updates.py`, `test_updater.py` | Update checks with GitHub faked (versions, once-a-day timing, state, announcing once) and the one-click updater with git, uv and the installer faked (safety checks, step order, failures) |
| `test_install_gearth.py` | The installer on macOS, Windows and Linux: finding a running G-Earth, the folder search, `--gearth-dir`, virtualenv paths |

Real packets captured from the live client are used as fixtures (`LIVE_USER_UPDATE`, `LIVE_BOT_ENTRY`, `LIVE_PLAYER_PACKET`). When the client changes again, add the new capture as a test first.

## Adding an avatar action

This is how `expression_weight`, `sign_weight` and `look_weight` were added:

1. **`protocol.py`:** add a `build_…` function, or reuse `HPacket(name, *ints)`, and any value constants.
2. **`config.py`:** add a weight to `AvatarConfig` and include it in the weights validation.
3. **`avatar.py`:**
   - add an option in `_autopilot_action()`
   - if it's a new packet type, add it to `ACTION_PACKETS` and `HOW_TO_UNLOCK`
4. **Config files:** add the key to `config.toml` and `config.example.toml`.
5. **Settings window:** add a `Field` to the "How often each action" box in `settings/schema.py` (`test_settings_schema.py` fails until every config key is shown or listed in `HIDDEN`).
6. **Tests:** add a test in `test_avatar.py`; `run_autopilot()` helps here.
7. **Run checks:** `uv run pytest && uv run ruff check .`, then restart the bot.

## Adding a chat command

1. **`config.py`:** add the name to `COMMAND_NAMES` and a default in `COMMAND_DEFAULTS`.
2. **`commands.py`:** add a `CommandSpec` to `SPECS`.
3. **`prompts.py`:** add an entry to `ADDENDA`, and handle it in `build_user_message()`.
4. **Config files:** add a `[commands.<name>]` section.
5. **Settings window:** add a `_command(...)` box to the Commands page in `settings/schema.py`, and list the keys it doesn't use in `HIDDEN`.
6. **Tests:** add tests in `test_commands_prompts.py` and `test_bot.py`.
