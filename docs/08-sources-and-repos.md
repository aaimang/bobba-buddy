# Sources and repositories

[← Contents](MAIN_CONTENT.md)

The sources used to build and debug this bot. Where a file was read, the path is given so you can go back to it.

## G-Python (Python extension library)

**Repo:** [github.com/sirjonasxx/G-Python](https://github.com/sirjonasxx/G-Python). **Package:** [pypi.org/project/g-python](https://pypi.org/project/g-python/) (0.1.6, the version this project uses).

| File | What it taught |
|---|---|
| `g_python/gextension.py` | Extension lifecycle, the G-Earth socket protocol, the single packet thread, intercept modes, `send_to_server`, events |
| `g_python/hpacket.py` | Packet byte layout, `read_*` / `append_*`, the Latin-1 default, building packets by name |
| `g_python/hmessage.py` | `HMessage` (packet, direction, `is_blocked`) |
| `g_python/hparsers.py` | `HEntity`, `HUserUpdate`, `HUserProfile`, `HFloorItem`, enums (directions, signs, dances, stances) |
| `g_python/htools.py` | `RoomUsers` / `RoomFurni` / `Inventory` helpers, and the outdated `GetHeightMap` request |
| `tests/extension_console.py`, `tests/packets_example.py` | Example chat interception and sending |

The installed copy was read directly from `site-packages/g_python/`.

## G-Earth (the packet proxy)

**Repos:** [github.com/G-Realm/G-Earth](https://github.com/G-Realm/G-Earth) (maintained; branch `moon`) and [github.com/sirjonasxx/G-Earth](https://github.com/sirjonasxx/G-Earth) (original).

| File (G-Realm, `moon`) | What it taught |
|---|---|
| `G-Earth/src/main/java/gearth/app/protocol/HConnection.java` | `canSendPacket`: the packet safety rule and Developer mode |
| `G-Earth/src/main/java/gearth/app/protocol/connection/packetsafety/PacketSafetyManager.java`, `SafePacketsContainer.java` | "Seen" packets are tracked per hotel version for as long as G-Earth is open |
| `G-Earth/src/main/java/gearth/app/services/extension_handler/ExtensionHandler.java` | Unsafe sends are dropped and logged, not sent |
| `G-Earth/src/main/java/gearth/app/services/extension_handler/extensions/implementations/network/NetworkExtensionCodec.java` | Extension message types; Latin-1 transport keeps bytes intact |
| `G-Earth-Api/src/main/java/gearth/protocol/HMessage.java`, `HPacket.java` | How packets are turned into text for extensions |
| `G-Earth-Api/src/main/java/gearth/extensions/parsers/HEntityUpdate.java` | G-Earth's own `UserUpdate` parser (documented layout) |
| `G-Earth/src/main/java/gearth/app/services/extension_handler/extensions/implementations/network/executer/ExecutionInfo.java`, `G-Earth/pom.xml` | Where `Extensions/` lives: `gearth.data.dir` (set only by the macOS launcher) or the folder with `G-Earth.jar` |
| `G-Earth/src/main/java/gearth/app/services/packet_info/PacketInfoManagerRemote.java`, `providers/implementations/SulekPacketInfoProvider.java` | For Flash only Sulek is used; it strips name suffixes and fetches by hotel version |

Also from your own G-Earth folder (`~/Library/Application Support/G-Earth/` on macOS; the `G-Earth.jar` folder on Windows and Linux): `Extensions/*/command.txt`, the extension launch format.

## G-Rust (Rust extension framework)

**Repo:** [github.com/G-Realm/G-Rust](https://github.com/G-Realm/G-Rust). This was the main source for packet layouts.

| File | What it taught |
|---|---|
| `src/extension/parsers/incoming.rs` | `Chat`/`Shout`/`Whisper`, `Users`, `UserUpdate`, `UserRemove`, `UserObject`, `RoomReady`, `FloodControl`, `Dance`, `Expression`, `Sleep`, `FloorHeightMap`, `HeightMap`, `RoomEntryTile`, `RoomEntryInfo`, `GetGuestRoomResult` |
| `src/extension/parsers/outgoing.rs` | `Chat`, `Shout`, `Whisper`, `MoveAvatar`, `Dance`, `ChangePosture`, `AvatarExpression`, `Sign`, `LookTo`, `GetGuestRoom`, `OpenFlatConnection` |
| `src/extension/parsers/subparsers.rs` | `User` (player/pet/bot tails), `UserUpdateMessageData` and status parsing, `HeightMapTile` encoding, `Gesture`/`Posture` |
| `src/protocol/vars/legacy.rs` | `LegacyId` / `LegacyDouble` / `LegacyStringId`: int vs long, and doubles as strings, depending on client type |
| `examples/chat-logger/src/main.rs` | Mapping room index → name from `Users` / `UserRemove` |

## xabbo (C# library, cross-check)

**Repo:** [github.com/xabbo/core](https://github.com/xabbo/core).
- `src/Xabbo.Core/Avatar/AvatarStatus.cs`, `src/Xabbo.Core/Messages/Incoming/AvatarUpdatesMsg.cs`, `src/Xabbo.Core/Util/Tile.cs`: a third independent `UserUpdate` parser (documented layout), used to rule out a mistake in the bot's own parser.
- The `AvatarChatMsg`, `ChatMsg`, `WhisperMsg` and `ShoutMsg` message classes: chat layouts.

## Packet name catalogue

**Sulek API:** `https://api.sulek.dev/releases/MAC63-202610131318-613990583/messages`, the names and ids for your exact build (595 incoming, 576 outgoing messages). Used to confirm names, find ids, and discover that `GetHeightMap` is gone.

## Nitro (HTML5 client, for reference)

[billsonnn/nitro-renderer](https://github.com/billsonnn/nitro-renderer) and [billsonnn/nitro-react](https://github.com/billsonnn/nitro-react): `FloodControlParser`, `RoomUnitChatParser`, and the 100-character chat input limit.

## Claude API

- **Anthropic Python SDK** `anthropic` 1.x: `messages.create`, `output_config.effort`, typed errors, `usage.server_tool_use.web_search_requests`.
- **Model:** `claude-haiku-5-5`. Thinking is adaptive (on by default); no `temperature` or prefill.
- **Web search:** `web_search_20250305` (the version for Haiku), with `pause_turn` handling.

## Other

- **wikiHow, "Good Roasts":** techniques for the `/roast` prompt (pasted into the chat, since it couldn't be fetched).
- **Your own captures** in `logs/bad-packets/` and `logs/bobba-buddy.log`: the only source for the extra ints in `UserUpdate` and `Users`.
