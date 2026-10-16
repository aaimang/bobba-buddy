# Ask Claude: a Habbo chat bot for G-Earth

A G-Earth extension in Python for the Habbo AIR client. It answers whitelisted chat commands with Claude, and gives your avatar a life of its own.

**Chat commands**

| Command | What it does |
|---|---|
| `/q <question>` | Answers a question. Remembers your last few questions for 10 minutes and can search the web. |
| `/roast [name]` | Gives a PG roast of someone in the room, based on their name and motto. With no name, it roasts you. |
| `/suggest <topic>` | Gives 2–3 quick ideas, e.g. `/suggest room theme for a cafe`. |
| `/joke [topic]` | Tells one clean joke. |
| `/q reset` | Forgets your `/q` history. |
| `/q` | Lists the commands. |

Replies come back the way the question was asked: chat gets chat, shout gets shout, whisper gets whisper. Each reply is split into bubbles of up to 100 characters (Habbo's limit), sent 2 seconds apart.

**Your avatar** (see [Avatar behaviour](#avatar-behaviour)):

- **Copying:** anyone standing within 1 tile of you gets copied. That covers dances, wave and thumbs up, signs, and sitting or standing.
- **Autopilot:** every 30–90 seconds your avatar does something random. It walks (and waves), sits or stands, dances or stops, waves or gives a thumbs up, holds up a sign, or turns to look around.
- **Anti-idle:** your avatar never goes to sleep (Zzz).
- **Staying in your room:** after a bot restart, or if you end up on the hotel view, it takes you back to your last room. See [Staying in your room](#staying-in-your-room).

> ⚠️ **Running this on an official hotel breaks Habbo's [Terms of Service](https://www.habbo.com/playing-habbo/terms-of-service) and can get the account banned.**
> - **Section 5 (Code of Conduct)** forbids automation software, bots, scripts and unauthorized third-party tools (G-Earth and its extensions count) meant to gain an unfair advantage or interfere with the Service.
> - **Section 8 (Termination)** lets Sulake suspend or permanently ban an account for a breach, or a suspected breach, and block access by IP address or machine ID.
> - **Rooms that run mainly on automated activity** may be left out of the Navigator's room lists.
>
> The whitelist, cooldowns and pacing lower the risk but don't remove it. The autopilot and anti-idle features in particular make an account look automated.

## Setup

Requirements: macOS, [G-Earth](https://github.com/G-Realm/G-Earth) with the Habbo AIR client, Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
cd ~/Desktop/habbo-ask-bot
uv sync
cp .env.example .env                 # then paste your ANTHROPIC_API_KEY into .env
cp config.example.toml config.toml   # then put your friends' names in `whitelist`
uv run python scripts/smoke_claude.py   # optional: one live call per command (~$0.02)
```

The example config has `rejoin_on_start` and `return_on_exit` off. Turn them on in `config.toml` if you want them.

## Running it

**From a terminal.** Start G-Earth, connect to Habbo, then run:

```bash
uv run python -m habbo_ask_bot -p 9092
```

- Use the port shown in G-Earth's Extensions tab.
- G-Earth asks you to allow the connection; click allow.
- If G-Earth restarts, the bot reconnects on its own. Ctrl-C stops it.
- Double-clicking the extension in G-Earth turns everything on and off.

**From G-Earth's Extensions list.**

```bash
uv run python scripts/install_gearth.py
```

Restart G-Earth and "Ask Claude" appears in the Extensions tab. Remove it with `--uninstall`.

### Restarting and reloading the room

| You changed… | Restart the bot? | Re-enter the room? |
|---|---|---|
| `config.toml` | No: applies within ~2 s (except `workers` and `max_pending`) | No |
| The bot's code (an update) | Yes: Ctrl-C, then run it again | Only if `rejoin_on_start = false` |

After a restart, the bot needs the room's user list and floor map. With `rejoin_on_start = true` it re-enters your last room itself. Otherwise, leave and re-enter the room once (your own commands work without it). The bot can't just ask the server to resend the room: the packet for that (`GetHeightMap`) doesn't exist in the current AIR client.

### Developer mode (G-Earth packet safety)

**Turn on Developer mode** in G-Earth (Extra tab) so the bot can chat and act immediately.

G-Earth refuses to send a packet from an extension unless it has already seen your client send that packet type since G-Earth was opened ([`HConnection.canSendPacket`](https://github.com/G-Realm/G-Earth/blob/moon/G-Earth/src/main/java/gearth/app/protocol/HConnection.java)).

- **Dropped packets are harmless.** A refused packet never reaches Habbo; G-Earth just logs "attempted to send an unsafe packet".
- **Developer mode is the only switch that lifts this check.** Packet expressions like `{out:Dance}{i:1}` end up as the same packet id, so they hit the same check.
- **The usual Developer-mode risk doesn't apply here.** That risk is an extension sending a wrong packet id. This bot uses the ids from the catalogue for your exact client build.

**Without Developer mode,** do each action once by hand: chat (plus shout or whisper), walk, sit, dance, wave, hold up a sign, and open a room from the navigator. G-Earth then allows it until you close G-Earth, even if you restart the bot.

By default the bot always tries to send and lets G-Earth decide. Set `require_seen_packets = true` (under `[chat]` and/or `[avatar]`) to make it wait until it has seen you do an action. That avoids G-Earth's log warnings, and shout replies then fall back to chat.

## Configuration (`config.toml`)

Changes are picked up within about 2 seconds. If an edit is invalid, the error shows in the G-Earth console and the previous settings stay active. Every option is commented in `config.example.toml`. The ones you'll most likely change:

- **`whitelist`:** who can use the chat commands. Names are case-insensitive. You are always allowed.
- **`[commands.<name>]`:** per-command `enabled`, `prefix`, `cooldown_s`, `max_chars`, `max_bubbles`, and `web_search` (for `q`/`suggest`).
- **`[commands.roast]`:**
  - `opt_out`: people who are never roasted.
  - `target_cooldown_s`: how long before the same person can be roasted again.
- **`[limits] daily_budget_usd`:** the bot stops answering once this is spent and starts again at midnight. `0` turns the cap off. See [Costs](#costs).
- **`[claude] model`:** `claude-haiku-5-5` by default.
- **`[avatar]`:** copying, autopilot weights, anti-idle, and the Habbo Club switches `hc_dances` / `hc_expressions`. See [Avatar behaviour](#avatar-behaviour).
- **`[room]`:** `rejoin_on_start` and `return_on_exit`. See [Staying in your room](#staying-in-your-room).

If Habbo doesn't send chat that starts with `/`, change the prefixes to `!q`, `!roast`, etc.

## Avatar behaviour

All of this is set in the `[avatar]` section of `config.toml`. Your avatar is the logged-in account.

**Habbo Club.** Without HC you only have dance 1, wave and thumbs up. The bot sticks to those unless you set `hc_dances = true` (dances 2–4) or `hc_expressions = true` (blow a kiss, laugh, jump). This applies to both copying and autopilot.

**Copying.** When a player stands within `mimic_radius` tiles of you (1 = the 8 tiles around you), your avatar copies them:

| They… | You… |
|---|---|
| dance / stop dancing | dance the same / stop. HC dances become dance 1 without `hc_dances`. |
| wave or give a thumbs up | do the same (also blow kiss, laugh, jump with `hc_expressions`) |
| hold up a sign | hold up the same sign |
| sit / stand up | sit on the floor / stand up |

This applies to anyone nearby, not just the whitelist. Pets and bots are ignored. Each kind can be turned off with `mimic_dance`, `mimic_expressions`, `mimic_signs` or `mimic_posture`.

**Autopilot.** Every `autopilot_min_s`–`autopilot_max_s` seconds (30–90), your avatar picks one action, weighted by the `*_weight` settings. Set a weight to 0 to turn that action off.

| Action | Setting | Default weight |
|---|---|---|
| Walk to a random tile within `walk_radius` tiles, then wave (`wave_on_walk`). It never picks the door tile (that leaves the room), a tile someone is on, or a wall. | `walk_weight` | 5 |
| Sit down, or stand up if already sitting | `sit_weight` | 2 |
| Start dancing, or stop if already dancing | `dance_weight` | 2 |
| Wave or thumbs up (plus the HC expressions with `hc_expressions`) | `expression_weight` | 2 |
| Hold up a random sign: 0–10, heart, skull, !, soccer ball, smile, red or yellow card | `sign_weight` | 1 |
| Turn to look in a random direction | `look_weight` | 2 |

It pauses for `owner_pause_s` (60 s) whenever you move or act yourself, and for `mimic_pause_s` (30 s) after copying someone, so it doesn't walk away mid-copy.

**Anti-idle.** Your client normally tells the server you're idle after a few minutes without input, and the server then shows Zzz. The bot blocks that report. If the server marks you asleep anyway, the bot wakes you with the first available of `wake_actions`:

- `"wave"`: a wave
- `"walk"`: a step to an adjacent tile
- `"look"`: turning your head

Avatar actions are sent at least `action_gap_s` (1.5 s) apart. Copies take priority over autopilot, and any copy older than 5 seconds is dropped.

## Staying in your room

Two options under `[room]` keep you in your room:

- **`rejoin_on_start`:** after the bot restarts, it waits `rejoin_delay_s` (3 s) and, if no room has loaded, re-enters the last room it saw you in.
- **`return_on_exit`:** if you end up on the hotel view (left by accident, disconnected from the room), the bot takes you back to the last room after `return_delay_s` (5 s).
  - Moving to another room doesn't trigger it, because the new room loads before the wait ends.
  - It tries at most `return_max_attempts` (3) times in a row, waiting longer each time, then stays out. That covers kicks, bans, and doorbell or password rooms.
  - Staying in the room for a minute resets the count.
  - To go to the hotel view on purpose, double-click the extension in G-Earth (bot OFF) first, or set `return_on_exit = false`.

Both use the same request the navigator sends when you click a room. The last room is saved in `state/last_room.json`. Habbo has no way to ask "which room am I in?", so the bot can only go back to rooms it has seen you enter. If you changed rooms while the bot was stopped, it takes you back to the last one it saw.

## Costs

```bash
uv run python scripts/cost_report.py              # today vs the cap, last 7 days, by command and person
uv run python scripts/cost_report.py --date 2026-10-13
uv run python scripts/cost_report.py --days 30
```

- **`state/budget.json`:** today's running total, which the daily cap checks. It resets at local midnight.
- **`logs/askbot.log`:** one line per Claude call, with who asked, tokens, web searches, estimated cost and request id.

Most of the cost is web searches: $0.01 each, plus the search results Claude reads. Plain answers cost fractions of a cent. To spend less, set `web_search_max_uses = 1` under `[claude]`, or `web_search = false` under `[commands.q]`. These are the bot's own estimates; Anthropic's billing page is the authoritative number.

## How it works

```
Habbo client ⇄ G-Earth ⇄ (socket) ⇄ this extension
                                  ├─ intercepts: Chat/Shout/Whisper (both directions), Users, UserRemove,
                                  │   RoomReady, CloseConnection, UserObject, FloodControl, RemainingMutePeriod
                                  ├─ gate: command → whitelist → cooldowns → one request per user → queue → budget
                                  ├─ worker pool → Claude Messages API (web search for /q)
                                  ├─ outbox: sanitize → ≤100-char bubbles → one every 2 s, paused while muted
                                  ├─ avatar worker: copies (priority) / autopilot / wake-ups, ≥1.5 s apart
                                  │   ← UserUpdate, Dance, Expression, Sleep, FloorHeightMap, RoomEntryTile
                                  │   → MoveAvatar, Dance, ChangePosture, AvatarExpression, Sign, LookTo
                                  └─ room keeper: RoomReady → state/last_room.json; restart / CloseConnection → GetGuestRoom
```

- **Threading.** G-Python runs every packet callback on a single thread, and G-Earth holds each packet until the callback finishes. A slow callback would freeze the game, and a crash would stop packets entirely. So callbacks only parse and queue, wrapped in `guarded`. Claude calls, chat replies and avatar actions each run on their own threads.
- **Packet layouts** follow [G-Realm/G-Rust](https://github.com/G-Realm/G-Rust/tree/main/src/extension/parsers), with fixes for the current AIR client (seen on `MAC63-202610131318`). Packets are always addressed by name, because header ids differ between the Windows and macOS AIR builds.
  - **`UserUpdate`** entries have an extra int after the two directions.
  - **`Users`** player entries end with an extra int.
  - **Fallbacks:** the parsers also accept the documented layouts. If a user entry still doesn't match, the parser skips ahead to the next one instead of losing the rest of the room.
- **Bad packets:** the first packet of each type that doesn't parse is logged once and saved whole to `logs/bad-packets/`, so layout changes can be worked out.
- **Your own `/q`** is read from your outgoing packet, which is how whisper replies know their target. It is never blocked, so the room still sees what you typed. The server's echo of it is ignored, so you don't get answered twice.
- **Memory** keeps only plain question and answer text, in memory, and is cleared on restart.

## Development

```bash
uv run pytest        # offline test suite (fake G-Earth + fake Claude)
uv run ruff check .  # lint, including Google-style docstrings
uv run --group docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html   # HTML docs
```

Logs go to `logs/askbot.log` (rotating, about 6 MB kept).

**Versioning.** Current version: **1.0.0**. Releases follow [semantic versioning](https://semver.org) and are tagged `vMAJOR.MINOR.PATCH` (for example `v1.0.0`). Bump the version in both `pyproject.toml` and `src/habbo_ask_bot/__init__.py`. G-Earth's Extensions list shows this version too, so rerun `scripts/install_gearth.py` after a bump. See [Version history](docs/MAIN_CONTENT.md#version-history).

## Troubleshooting

| Symptom | Fix |
|---|---|
| Replies or avatar actions don't show up; G-Earth logs "attempted to send an unsafe packet" | Turn on Developer mode (Extra tab), or do that action once by hand. See [Developer mode](#developer-mode-g-earth-packet-safety). |
| Console: "Re-enter the room" | The bot doesn't have the user list. Turn on `rejoin_on_start`, or leave and re-enter the room. |
| Console: "No saved room to rejoin yet" | Enter a room once with the bot running; it's remembered from then on. |
| It keeps taking me back to a room | That's `return_on_exit`. Turn the bot OFF (double-click) before leaving on purpose, or set it to `false`. |
| Copying or autopilot does nothing | The bot needs your user list and floor map, so check that the room loaded after the bot started (see above). Copying only reacts to players within 1 tile. |
| Blow kiss / laugh / jump / dances 2–4 don't happen | They need Habbo Club. Leave `hc_expressions` / `hc_dances` off without HC. |
| Console: "say anything in chat once", "…once by hand", "Autopilot is waiting" | Only with `require_seen_packets = true`. Do the action once by hand, or set it to `false` and use Developer mode. |
| Console: "Unknown packet names…" | G-Earth's packet info for this client version is missing. Update G-Earth. |
| Console: "Claude API error, bot paused" | Fix the API key or model in `.env`/`config.toml`, then double-click the extension. |
| A file appears in `logs/bad-packets/` | Habbo changed a packet layout. The bot keeps working around it; share the file to get the parser updated. |
| Nothing happens at all | Check that the name is in `whitelist`, that the bot is ON (double-click toggles it), and look in `logs/askbot.log`. |
