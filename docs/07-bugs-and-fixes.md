# Bugs and fixes

[← Contents](MAIN_CONTENT.md)

Everything that went wrong while building this bot, in the order it came up.

## Packets and the live client

### 1. `UserUpdate`: "truncated string" errors, and sit/sign never detected
- **Symptom:** occasional `handler _on_user_update failed … ProtocolError: truncated string` tracebacks. Copying sit, stand and signs silently never worked.
- **Cause:** the live AIR client (`MAC63-202610131318`) sends an **extra int after the head and body directions** in every `UserUpdate` entry. G-Earth, G-Rust and xabbo all document the layout without it.
  - **With one entry,** the bot read the extra int as an empty status. No error, but the real status (`/sit…/`, `/sign…/`) was lost.
  - **With two or more entries,** the second entry was read from the wrong position, which caused the error.
- **How it was found:**
  1. The guard was changed to log the first bad packet with a hex dump.
  2. The dump showed 12 zero bytes between the height and the status string instead of 8.
- **Fix:** `parse_user_updates` reads the extra int. It tries the live layout (1 extra int) first, then the documented one (0). A layout only counts if it reads the whole packet with sensible values. The capture is a test (`LIVE_USER_UPDATE`).

### 2. `Users`: players after an odd entry went missing
- **Symptom:** a `Users` packet (25 people) failed. Everyone after the failing entry was lost, so their commands, roasts and copying didn't work.
- **Cause:** player entries end with an **extra int (-1) after `isModerator`**. Confirmed from a saved one-player packet (".Han.").
- **Fix:**
  - `parse_users` reads the extra int for players (falling back to the documented layout).
  - If a tail still doesn't match, it **scans forward to the next valid entry**. A valid entry needs a sensible name, motto, look, index, coordinates, direction 0–7 and type 1–4.
  - Worst case is about 1 ms for a full room.
  - Fixtures: `LIVE_BOT_ENTRY`, `LIVE_PLAYER_PACKET`.

### 3. The log cut off the part of the packet that mattered
- **Symptom:** the hex dump in the warning stopped at 400 bytes, but the problem was further in.
- **Fix:** the first bad packet of each type per session is now **saved whole** to `logs/bad-packets/<Name>-<time>.hex`, and the warning shows the path.

### 4. `GetHeightMap` doesn't exist anymore
- **Symptom:** after a bot restart, nothing worked for other people ("Re-enter the room" notice), and copying and autopilot were idle.
- **Cause:** the bot only learns about a room as you enter it. G-Python's `RoomUsers.request()` and the bot's reload both send `GetHeightMap`, which isn't in the current client's packet list. Nothing else asks the server to resend the room.
- **Workaround:** re-enter the room.
- **Fix:** `rejoin_on_start` remembers the last room (`state/last_room.json`) and sends `GetGuestRoom(id, 0, 1)` after a restart.

### 5. G-Python reads strings as Latin-1
- **Cause:** `HPacket.read_string()` defaults to `iso-8859-1`; Habbo uses UTF-8. Names and chat with accents or emoji came out garbled.
- **Fix:** the bot's own `_read_str` always decodes UTF-8 and rejects truncated strings, which G-Python doesn't check.

### 6. Wrong guess about why some packets didn't parse
Before finding #1, the first theory was that G-Earth sent the bot packets of the wrong type, or garbled bytes above 0x7F in transit. Checking G-Realm's code ruled both out:
- **Names are unique:** the Sulek catalogue maps every name to exactly one id.
- **Transport is byte-perfect:** it uses Latin-1 both ways.

Lesson: compare a real captured packet against the layout before blaming the tools.

## G-Earth and G-Python

### 7. Actions needed doing by hand once; worse after every restart
- **Symptom:** console notices like "Do these once by hand" and "Autopilot is waiting". After each restart the bot asked again.
- **Cause:**
  - **G-Earth's packet safety:** extensions can only send packet types G-Earth has seen from the client (see [G-Earth requirements](06-gearth-requirements.md#packet-safety-and-developer-mode)).
  - **The bot's own copy of that list was reset** on every reconnect, but G-Earth keeps its list for as long as it's open. So the bot blocked actions G-Earth would have allowed.
- **Fix:** send by default and let G-Earth decide (`require_seen_packets = false`). Recommend Developer mode. Packet expressions don't get around it.

### 8. A slow or failing callback would freeze or kill the client
- **Cause:** G-Python runs all callbacks on one thread with no `try/except`, and G-Earth waits for each answer.
- **Fix:**
  - Every callback is wrapped in `guarded`.
  - Claude calls run on worker threads.
  - Replies and avatar actions run on their own threads.
  - `mode='async'` is avoided, because it shares the packet object across threads.

### 9. `Extension.start()` hangs if G-Earth refuses the connection
- **Cause:** g-python 0.1.6 waits on a barrier for INIT, which never comes if the "allow?" prompt is declined.
- **Fix:** `start_extension()` in `__main__.py` watches the socket and breaks the barrier.

### 10. G-Python's GitHub `main` branch doesn't import
- **Cause:** `hpacket.py` and `gextension.py` import each other.
- **Fix:** pin `g-python==0.1.6` from PyPI.

### 11. Smaller G-Python problems
- **`HBubble` enum:** written with `:` instead of `=`, so it has no members. Use plain ints.
- **`hash is not 'NULL'`:** an identity comparison, so the `'NULL'` string is never replaced with `None`. Harmless, but it's the cause of the `SyntaxWarning` on Python 3.12+.

## Chat behaviour

### 12. Wasted API call when a reply couldn't be sent
- **Cause:** the reply mode check (can G-Earth send a whisper yet?) happened after the Claude call.
- **Fix:** `_reply_mode` is checked in `handle()` before anything is spent.

### 13. Whispered answers could have leaked into public chat
- **Cause:** an early design fell back to normal chat when whispers weren't allowed yet.
- **Fix:** whispers never fall back. Shouts fall back to chat, but only with `require_seen_packets = true`.

### 14. Too-long answers ("3 bubbles for a simple question")
- **Cause:** the prompt said "at most 250 characters", which the model treated as a target.
- **Fix:** the `/q` (then `/ask`) prompt now says "as briefly as possible; a simple question gets one short sentence", and the persona is a chill Habbo player. `max_bubbles` still caps it.

### 15. Web-search preamble in answers
- **Cause:** with web search, the model often writes "Let me look that up" before searching.
- **Fix:** `extract_answer()` keeps only the text after the last search step.

## Avatar behaviour

### 16. HC-only actions did nothing on a non-HC account
- **Cause:** blow kiss, laugh and jump (expressions 2, 3, 6) and dances 2–4 need Habbo Club.
- **Fix:** without `hc_expressions` / `hc_dances`, autopilot and copying only use wave, thumbs up and dance 1. Expression 4 was dropped entirely; it isn't in the client's list.

## Development

### 17. Config typo stopped the bot
- **Symptom:** `[commands.ask] unknown key(s): refix`. (That section is now `[commands.q]`.)
- **Cause:** `refix = "/q"` instead of `prefix`. The config rejects unknown keys on purpose, so a typo can't be silently ignored.
- **Fix:** correct the key. (The error's hint to "copy config.example.toml" doesn't apply to typos.)

### 18. Test suite slowed from 1 s to 30 s
- **Cause:** `Outbox.stop()` waited up to 0.5 s for its thread to notice.
- **Fix:** `stop()` puts a wake-up item on the queue, so the suite takes about 0.6 s.

### 19. Fallback parsing picked the wrong layout
- **Cause:** when no layout fit, the first version took the attempt with the most entries. The old layout can "decode" an entry by misreading the extra int as an empty status.
- **Fix:** the documented layout only wins on a complete, clean parse; otherwise the live layout's partial result is used.

### 20. A test bug that looked like a bot bug
- **Cause:** the test queued a copy and then advanced the clock 91 s, so the copy was correctly dropped as stale.
- **Fix:** the test now triggers the copy after advancing the clock. The bot was right.

### 21. wikiHow couldn't be fetched
- **Cause:** Claude Code can't fetch from wikihow.com.
- **Workaround:** the page was pasted into the chat. Its roast techniques (comparisons, exaggeration, backhanded compliments, rhymes) shaped the `/roast` prompt; no lines were copied.

## Installing

### 22. G-Earth runs but its window never opens
- **Symptom:** after opening G-Earth's `Extensions` folder in Finder, G-Earth stopped showing its window. The process kept running, and restarting didn't help. `~/Library/Logs/G-Earth/app.log` ended with `Running extension …/Extensions/.DS_Store`.
- **Cause:** Finder writes a hidden `.DS_Store` into every folder you open. G-Earth 1.5.4 tries to start **every** entry in `Extensions/` as an extension. `.DS_Store` has no launch command, so JavaFX's start-up crashes with a `NullPointerException` (`ExecutionInfo.getExecutionCommand(String)` returns `null`) before the window is shown.
- **Fix:** delete the file (`rm "$HOME/Library/Application Support/G-Earth/Extensions/.DS_Store"`). Since 1.2.3, `scripts/install_gearth.py` removes a stray `.DS_Store` from `Extensions/` and from its own extension folder on every run, including `--uninstall` (`--dry-run` only reports it). Other extensions' folders aren't touched.
