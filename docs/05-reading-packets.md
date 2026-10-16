# Reading Habbo packets

[← Contents](MAIN_CONTENT.md)

## Wire format

Every Habbo AIR packet looks like this on the wire, big-endian throughout:

```
┌────────────┬──────────┬─────────────────────────┐
│ length (4) │ id (2)   │ payload                 │
└────────────┴──────────┴─────────────────────────┘
  int32: bytes   uint16:    fields back to back,
  after this     packet     no names, no separators
  field          header id
```

So payload reading always starts at **byte 6**. Field types:

| Type | Code | Bytes | Example |
|---|---|---|---|
| int | `i` | 4, signed | `00 00 03 a3` = 931; `ff ff ff ff` = -1 |
| string | `s` | 2-byte length, then UTF-8 bytes | `00 05 2e 48 61 6e 2e` = ".Han." |
| bool | `B` | 1 (`00`/`01`) | `00` = false |
| short | `u` | 2, signed | `00 17` = 23 |
| long | `l` | 8, signed | |
| byte | `b` | 1 | |

Things to know:
- **Decimal numbers are strings** on the AIR client. The tile height z is `"0.15"`, not a float.
- **Lists are an int count** followed by that many items.
- **A packet has no field names,** so you need to know the layout to read it. Read the wrong type once and every later field is misread. That's exactly what the bad-packet warnings were about ([Bugs](07-bugs-and-fixes.md)).

## G-Earth's packet logger

1. **Open the logger:** use G-Earth's packet logger window, or the **Packet Info Explorer** extension (installed in your G-Earth). The menu names vary between G-Earth versions.
2. **Find the packet:** packets show with their name (from Sulek) and direction. Filter by name, e.g. `UserUpdate`.
3. **Read the fields:** G-Earth shows the packet as an **expression**, with fields guessed from the bytes or taken from a known structure.

## G-Earth packet expressions

Expressions are G-Earth's text form of a packet:

```
{out:MoveAvatar}{i:5}{i:7}          walk to tile 5,7
{out:Dance}{i:1}                    dance
{out:AvatarExpression}{i:7}         thumbs up
{out:Sign}{i:11}                    heart sign
{out:LookTo}{i:-9999}{i:0}          face direction 7
{out:Chat}{s:"hello"}{i:0}{i:-1}    say "hello"
{in:Chat}{i:0}{s:"fake"}{i:0}{i:0}{i:0}{i:-1}   incoming: only your own client sees it
```

- **`{out:Name}` / `{in:Name}`** pick the direction and packet, by name. Names are safer than ids, which change with every client build.
- **`{i:…}` `{s:"…"}` `{b:…}` `{u:…}` `{l:…}`** are int, string, byte/bool, short and long fields, in order.
- **Sending:** G-Earth's **Injection** tab sends an expression.

From code, `ext.send_to_server("{out:Dance}{i:1}")` does the same thing as `HPacket("Dance", 1)`. Both become the same header id, so both are subject to the same [packet safety](06-gearth-requirements.md#packet-safety-and-developer-mode) check.

## Reading packets in G-Python

```python
from g_python.gextension import Extension
from g_python.hdirection import Direction
from g_python.hpacket import HPacket


def on_chat(msg):  # msg is an HMessage
    p = msg.packet  # HPacket; reading starts at byte 6
    index = p.read_int()
    text = p.read_string(encoding="utf-8")  # ALWAYS pass utf-8 (default is latin-1!)
    gesture, style = p.read("ii")  # several fields at once: i s B b u l
    # msg.is_blocked = True             # drop the packet (it never arrives)


ext = Extension({"title": "x", "description": "x", "version": "1", "author": "x"}, ["-p", "9092"])
ext.intercept(Direction.TO_CLIENT, on_chat, "Chat")  # by name, never by id
ext.start()

ext.send_to_server(HPacket("Chat", "hi", 0, -1))  # build by name; str/int/bool appended in order
```

Rules learned the hard way:
- **Encoding:** `read_string()` defaults to Latin-1, but Habbo uses UTF-8. Pass `encoding="utf-8"`. Use G-Python parsers with care, since they don't.
- **Never block or raise in a callback:** the game freezes or packets stop. See [How the bot works](01-how-the-bot-works.md#threads-and-the-one-rule-that-matters).
- **Truncation goes unnoticed:** `read_string()` doesn't check for it. The bot's `_read_str` in `protocol.py` does.
- **Reading position resets:** after each callback the packet's read position goes back to byte 6, so every callback reads from the start.

## Decoding hex by hand: a real example

From `logs/bad-packets/Users-20261013-233453.hex`, a `Users` packet with one player:

```
00 00 00 d0             length = 208 (+4 = 212 bytes total)
05 0b                   id = 1291 → Users
00 00 00 01             count = 1
03 fe 09 bd             id = 66980285
00 05 2e 48 61 6e 2e    name = ".Han."
00 0a 7c 55 4e … 7c     motto = "|UN| GOV |"
00 69 68 72 2d …        figure = "hr-5503-32.hd-6219-7-1364…" (105 bytes)
00 00 03 a3             room index = 931
00 00 00 24             x = 36
00 00 00 25             y = 37
00 04 30 2e 31 35       z = "0.15"
00 00 00 02             direction = 2 (east)
00 00 00 01             type = 1 → player
00 01 66                sex = "f"
00 08 11 4a             group id = 528714
00 00 00 03             group status = 3
00 16 3c 55 4e 3e …     group name = "<UN> Government [400k]"
00 00                   swim figure = ""
00 00 20 29             achievement score = 8233
00                      moderator = false
ff ff ff ff             ← extra int = -1, not in the documented layout
```

Method:
1. **Skip the first 6 bytes** (length and id).
2. **Read fields in layout order.** For each string, read 2 bytes for the length, then that many bytes.
3. **If a field looks absurd** (a string length larger than the packet, a coordinate in the millions, a direction above 7), you're out of step. The previous field was wrong or missing.
4. **Leftover bytes at the end** mean the layout has more fields than you know about.

## Decoding a bad-packet dump

When the bot can't parse a packet, it writes one warning in `logs/askbot.log`:

```
Users packet didn't match the expected layout (entry 0 of 1 (player '.Han.', index 931):
46-byte tail doesn't match the documented layout); … header=1291 length=212 saved=…/Users-….hex
```

The `saved=` file holds the whole packet in hex, 32 bytes per line, after two `#` comment lines. The warning tells you where it went wrong ("entry 0", "46-byte tail"). Decode from that point using the method above. Then:
1. **Add the bytes as a test fixture** in `tests/` (see `LIVE_PLAYER_PACKET` in `test_protocol.py`).
2. **Fix the parser** in `protocol.py`, keeping a fallback to the old layout.
3. **Run `uv run pytest`,** then restart the bot.

Only the first bad packet of each type is saved per session, so the folder doesn't fill up.

## Finding packet names and ids

- **For your exact build:** `https://api.sulek.dev/releases/<hotel version>/messages`, where the hotel version is printed in the bot's "Connected to …" notice. It lists names (`UserUpdateMessageEvent`) and ids. G-Earth drops the `MessageEvent`/`MessageComposer` suffix.
- **For layouts:** see G-Rust's parsers and xabbo in [Sources](08-sources-and-repos.md), then confirm against a live capture.
