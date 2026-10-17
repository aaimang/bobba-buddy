# G-Earth requirements

[← Contents](MAIN_CONTENT.md)

## Which G-Earth

| Project | Status | Use |
|---|---|---|
| [G-Realm/G-Earth](https://github.com/G-Realm/G-Earth) (branch `moon`, 1.5.4+) | **maintained**, the one to use | Supports Flash/AIR, Origins (Shockwave) and Nitro on Windows and macOS, and Unity on Windows |
| [sirjonasxx/G-Earth](https://github.com/sirjonasxx/G-Earth) | original; its README points to G-Realm | Same protocol for extensions |

- **Client type:** Habbo AIR is what G-Earth calls **`FLASH`**. The bot checks `client_type` on connect and warns if it isn't `FLASH`.
- **macOS:** G-Earth re-signs the AIR client found at `~/Library/Application Support/Habbo Launcher/downloads/air/<version>/Habbo.app`.

G-Earth's data folder depends on the OS. G-Earth uses the system property `gearth.data.dir` if it's set, and otherwise the folder that holds `G-Earth.jar` (`ExecutionInfo` in G-Realm's source):

| OS | Data folder | Why |
|---|---|---|
| macOS | `~/Library/Application Support/G-Earth/` | The macOS app's launcher sets `-Dgearth.data.dir` to this |
| Windows, Linux | The folder with `G-Earth.jar`, wherever G-Earth was unzipped | Nothing sets `gearth.data.dir` |

On Windows and Linux, `scripts/install_gearth.py` finds the folder itself: first from the command line of a running G-Earth (PowerShell `Win32_Process` on Windows, `/proc/<pid>/cmdline` and `cwd` on Linux), then by searching Downloads, Desktop and Documents (3 levels deep) and the home folder (1 level), within 10 seconds. A match counts only if `Extensions/` is next to the program or one folder up. `--gearth-dir` overrides both. The folder contains:

| Path | Contents |
|---|---|
| `Extensions/<Name>_<version>/command.txt` | how to launch each installed extension |
| `gearth-nitro-v2.crt` / `.key` | certificate for Nitro |

## Packet names: Sulek

For Flash/AIR, G-Earth downloads packet names and ids from **Sulek**, for your exact client build: `https://api.sulek.dev/releases/<hotelVersion>/messages`. It removes the `MessageEvent`/`MessageComposer` suffix, so `UserUpdateMessageEvent` becomes `UserUpdate`. The older Harble provider is offline and commented out in G-Realm's code.

Extensions get this table when the game connects (`connection_start`). Only after that can packets be built or intercepted by name.

## Packet safety and Developer mode

From G-Realm's source (`HConnection.canSendPacket`, `SafePacketsContainer`):

```java
return isPacketSendingAllowed(direction, packet) && (developerMode || isPacketSendingSafe(direction, packet));
```

- **Seen means safe.** A header id becomes safe once G-Earth has seen a packet with that id go by in that direction. The record is kept per hotel version for as long as G-Earth is open, and survives extension restarts.
- **Unsafe packets are dropped.** They never reach Habbo. G-Earth logs "Extension … attempted to send an unsafe packet, but had no permission".
- **Developer mode** (Extra tab) turns the check off.
- **Expressions don't get around it.** `{out:Dance}{i:1}` becomes the same header id.

This bot sends by default and lets G-Earth decide (`require_seen_packets = false`).
- **With Developer mode on,** everything works immediately.
- **Without it,** each action works once you've done it by hand: chat, shout, whisper, walk, sit, dance, wave, sign, and opening a room from the navigator.

G-Earth warns that Developer mode can get accounts banned. That warning is about sending wrong packet ids; this bot takes ids from the catalogue for your exact build.

## How extensions talk to G-Earth

Extensions connect over TCP to `127.0.0.1:9092`. If 9092 is taken, G-Earth counts up to the next free port; the Extensions tab shows the actual one. G-Earth-to-extension messages are G-Earth packets themselves (4-byte length, 2-byte type, payload):

| From G-Earth | Type | Meaning |
|---|---|---|
| INFO_REQUEST | 2 | "send me your title, author, version…" |
| PACKET_INTERCEPT | 3 | a packet in transit, as text `blocked\tindex\tTOCLIENT\t<packet>`, which **must** be answered |
| FLAGS_CHECK | 4 | answer to a flags request |
| CONNECTION_START | 5 | game connected: host, port, hotel version, client id, client type, packet infos |
| CONNECTION_END | 6 | game disconnected |
| INIT | 7 | extension accepted |
| ON_DOUBLE_CLICK | 1 | user pressed the extension's ▶ button (this bot opens its settings window) |

| To G-Earth | Type | Meaning |
|---|---|---|
| EXTENSION_INFO | 1 | the reply to INFO_REQUEST |
| MANIPULATED_PACKET | 2 | the (possibly changed or blocked) intercepted packet |
| SEND_MESSAGE | 4 | send a packet to the client or server |
| EXTENSION_CONSOLE_LOG | 98 | text for G-Earth's console |

- **Encoding:** packets travel as Latin-1 text, which preserves every byte (checked in G-Realm's `NetworkExtensionCodec`).
- **Every intercept waits for its answer:** G-Earth holds the packet until the extension answers, which is why a slow callback freezes the game.
- **Allow prompt:** without a cookie (`-c`), G-Earth shows an "allow this extension?" prompt.

## Python extensions (G-Python)

| Requirement | Detail |
|---|---|
| Library | **`g-python==0.1.6` from PyPI.** GitHub `main` fails to import (circular import between `hpacket.py` and `gextension.py`) |
| Python | 3.6+ for 0.1.6. This project uses 3.13 via uv. On 3.12+, 0.1.6 prints harmless `SyntaxWarning`s, which the bot silences in `gpython.py` |
| Dependencies | none |
| Start | `Extension(info, sys.argv, settings, silent)`, where `info` needs `title`, `description`, `version`, `author`, then `ext.start()`. Arguments: `-p port` (required), `-c cookie`, `-f filename` |
| Events | `init`, `connection_start`, `connection_end`, `double_click` (G-Earth's ▶ button; enabled when `use_click_trigger=True`) |
| Intercept modes | `default` (blocking, can modify), `async` (thread per packet; shares the packet object, which is racy), `async_modify` (blocks then re-sends; can reorder) |

Pitfalls in 0.1.6:
- **`read_string()` defaults to Latin-1.**
- **Callbacks have no exception handling.**
- **`start()` can hang** if G-Earth refuses the connection.
- **`HBubble` is broken.**
- **`htools.RoomUsers` uses `GetHeightMap`**, which no longer exists.

See [Bugs and fixes](07-bugs-and-fixes.md).

**Installing a Python extension in G-Earth:**
- **Through the Install button** (Developer mode only): G-Earth copies a single `.py` file into `Extensions/` and runs `python <file> -p … -f … -c …`. Use the `.py3` extension on macOS so it runs `python3`. Only that one file is copied, so helper modules must already be installed.
- **This project instead** uses `scripts/install_gearth.py`. It writes a `command.txt` that launches the project's own virtualenv with absolute paths. Apps started from Finder don't see your shell's environment, so the API key is read from `.env` via `--project-dir`.

## Java extensions

G-Earth itself is Java, and its native extension API is in `G-Earth-Api`. Store-installed Java extensions (like TradeBlocker and Packet Info Explorer in your G-Earth) use the same folder format:

```
Extensions/TradeBlocker_1.0/
├─ command.txt     ["java","-jar","TradeBlocker.jar","-c","{cookie}","-p","{port}","-f","{filename}"]
└─ extension/
   └─ TradeBlocker.jar
```

- **Running:** G-Earth replaces `{cookie}`, `{port}` and `{filename}` and runs the command. The jar is given as a relative path, so the working folder must be `extension/`.
- **Requirements:** a Java runtime on the system.
- **Node.js extensions** use the same scheme with `NodeExtensionSetup.jar`.

## Other frameworks

| Language | Library |
|---|---|
| Python | [sirjonasxx/G-Python](https://github.com/sirjonasxx/G-Python) (`pip install g-python`) |
| Rust | [G-Realm/G-Rust](https://github.com/G-Realm/G-Rust) (crate `g-rust`); its typed parsers were the main layout reference for this bot |
| Java | G-Earth's own `G-Earth-Api` |
| C# | [xabbo](https://github.com/xabbo); its `core` library has well-maintained message parsers |
