# Habbo packet reference

[← Contents](MAIN_CONTENT.md)

This covers every packet the bot reads or sends, for the **Habbo AIR client** (G-Earth calls it `FLASH`).

- **Ids** are for build `MAC63-202610131318-613990583`. They change with every client update and differ between Windows and macOS, so code always uses **names**. G-Earth maps names to ids for the build you're running.
- **Layout notation:** `i` = int32, `s` = string (u16 length + UTF-8 bytes), `B` = bool (1 byte), `u` = short (int16), `l` = long (int64). Full details in [Reading packets](05-reading-packets.md).
- **Direction:** *Incoming* = server → client (`TO_CLIENT`). *Outgoing* = client → server (`TO_SERVER`).

## Chat

| Name | Dir | Id | Layout | Bot uses it to… |
|---|---|---|---|---|
| `Chat` | in | 1536 | `i index, s text, i gesture, i style, i nLinks, (s, s, B)×n, i trackingId` | read room chat |
| `Shout` | in | 2737 | same as `Chat` | read shouts |
| `Whisper` | in | 1535 | same as `Chat` | read whispers to the owner (and the owner's own echo) |
| `Chat` | out | 2096 | `s text, i style, i tail` | read the owner's commands; send replies |
| `Shout` | out | 980 | `s text, i style` | same, for shouts |
| `Whisper` | out | 1875 | `s "target message", i style` | same; the target name and message share one string |
| `FloodControl` | in | 1199 | `i seconds` | pause replies while the owner is flood-muted |
| `RemainingMutePeriod` | in | 671 | `i seconds` | pause replies while muted by a moderator |

Notes:
- **Bubble limit:** 100 characters, counted in UTF-16 units, so an emoji counts as 2.
- **Outgoing `Chat`'s last int** is copied from the owner's own last chat; it defaults to -1.
- **The owner's chat** comes back as an incoming `Chat` with the owner's index. The bot ignores that echo.

## Room and users

| Name | Dir | Id | Layout | Used for |
|---|---|---|---|---|
| `Users` | in | 1291 | `i count`, then per entity a header + type-specific tail (below) | who's in the room, names, mottos, start positions |
| `UserUpdate` | in | 2863 | `i count`, then per entity `i index, i x, i y, s z, i headDir, i bodyDir, i <extra>, s status` | positions, sit/sign state |
| `UserRemove` | in | 3479 | `s index` (a number as a string) | someone left |
| `UserObject` | in | 1766 | `i id, s name, s figure, s sex, s customData, s realName, B, i, i, i, B, s, B, B` | the owner's own id and name |
| `RoomReady` | in | 3296 | `s roomModel, i roomId` | entered a room (saved for rejoin) |
| `CloseConnection` | in | 1796 | (empty) | left the room |
| `RoomEntryTile` | in | 1193 | `i x, i y, i dir` | the door tile (never walk onto it) |
| `FloorHeightMap` | in | 412 | `B smallScale, i wallHeight, s rows` | which tiles are floor |
| `HeightMap` | in | 3078 | width + per-tile shorts | (not used) |
| `RoomEntryInfo` | in | 2560 | `i roomId, B isOwner` | (not used) |
| `InfoRetrieve` | out | 2095 | (empty) | ask the server to resend `UserObject` |
| `GetGuestRoom` | out | 824 | `i roomId, i entering, i forward` | go to a room: `(id, 0, 1)` = like clicking it in the navigator |
| `OpenFlatConnection` | out | 1491 | `i roomId, s password, i ?` | (not used; the client sends it itself after `GetGuestRoom`) |
| `GetHeightMap` | out | — | — | **doesn't exist in this build**; the bot can't ask for a room reload |

### `Users` entry layout

Every entry starts with the same **header**:

```
i id, s name, s motto, s figure, i roomIndex, i x, i y, s z, i direction, i type
```

`type`: 1 = player, 2 = pet, 3 = old-style bot, 4 = bot. Then a **tail** that depends on the type:

| Type | Tail |
|---|---|
| 1 player | `s sex, i groupId, i groupStatus, s groupName, s swimFigure, i achievementScore, B isModerator, i <extra>` |
| 2 pet | `i subType, i ownerId, s ownerName, i rarity, B×6 (saddle, riding, breed, harvest, revive, breeding permission), i level, s posture` |
| 3 old bot | (nothing) |
| 4 bot | `s sex, i ownerId, s ownerName, i nSkills, u×n skills` |

`<extra>` marks fields the live client sends but public documentation doesn't list. Both were found while building this bot:
- **`UserUpdate`:** an int after the two directions, seen as 0.
- **`Users` players:** an int after `isModerator`, seen as -1.

The parsers try the live layout first and fall back to the documented one. See [Bugs and fixes](07-bugs-and-fixes.md).

### `UserUpdate` status string

The last field is a `/`-separated list of `key value` pairs:

```
/flatctrl 4/sit 0.55 1/sign 7/mv 12,5,0.0/
```

| Key | Meaning |
|---|---|
| `flatctrl N` | room rights level |
| `mv x,y,z` | walking to tile x,y |
| `sit z canStand` | sitting at height z |
| `lay z` | lying down |
| `sign N` | holding sign N (0–17) |
| `gst X` | gesture (smile, sad, …) |
| `wf` | skip position update |

## Avatar actions

| Name | Dir | Id | Layout | Values |
|---|---|---|---|---|
| `MoveAvatar` | out | 789 | `i x, i y` | target tile; the server works out the path |
| `LookTo` | out | 1823 | `i x, i y` | turn to face that point ([direction table](04-actions-directions-tiles.md#lookto-directions)) |
| `Dance` | out | 1065 | `i style` | 0 stop, 1 normal, 2–4 HC dances |
| `Dance` | in | 1996 | `i index, i style` | someone started or stopped dancing |
| `ChangePosture` | out | 3477 | `i posture` | 0 stand, 1 sit |
| `AvatarExpression` | out | 2886 | `i expression` | 1 wave, 2 blow kiss, 3 laugh, 5 idle, 6 jump, 7 thumbs up |
| `Expression` | in | 574 | `i index, i expression` | someone did an expression |
| `Sign` | out | 1716 | `i sign` | 0–17 ([table](04-actions-directions-tiles.md#signs-outsignin)) |
| `Sleep` | in | 805 | `i index, B sleeping` | someone fell asleep or woke up (Zzz) |
| `StartTyping` / `CancelTyping` | out | 278 / 1761 | (empty) | (not used) |

Incoming `Dance`, `Expression` and `Sleep` carry the **room index**, not the user id. G-Rust calls the field `user_id`, but it's the index.

## Profiles (`HUserProfile`)

The bot doesn't use profiles, but for reference:

| Name | Dir | Id | Layout (per G-Python's `HUserProfile`; not verified on the live build) |
|---|---|---|---|
| `GetExtendedProfile` | out | 1781 | starts with `i userId`; the rest is unverified, so check a live capture in G-Earth's packet logger |
| `ExtendedProfile` | in | 1359 | `i id, s username, s figure, s motto, s creationDate, i achievementScore, i friendCount, B isFriend, B requestSent, B isOnline, i nGroups, (group)×n, i lastAccessSeconds, B openProfile, B, i level, i, i starGems, B, B` |
| `RelationshipStatusInfo` | in | 2787 | (not used) |

A group entry in the profile is `i id, s name, s badge, s color1, s color2, B favourite, i ownerId, B hasForum`.

Given the extra ints found in `Users` and `UserUpdate`, treat the profile layout as unverified until it's checked against a live capture.

## Library parser classes

G-Python's `hparsers.py` has ready-made parsers. The bot uses its own versions in `protocol.py`, because the G-Python ones read strings as Latin-1 and don't know about the extra ints.

| Class | Parses | Notes |
|---|---|---|
| `HEntity` | one `Users` entry | `.id .name .motto .figure_id .index .tile .entity_type` (+ `.gender .favorite_group` for players) |
| `HUserUpdate` | one `UserUpdate` entry | `.index .tile .headFacing .bodyFacing .action .nextTile` |
| `HPoint` | a tile | `x, y, z` |
| `HFloorItem` / `HWallItem` | `Objects` / `Items` | furni |
| `HInventoryItem` | `FurniList` | inventory |
| `HUserProfile`, `HGroup`, `HFriends` | profile, groups, friend list | |
| `HDirection`, `HSign`, `HDance`, `HStance`, `HEntityType`, `HGender` | enums | `HBubble` is broken (written with `:` instead of `=`) |

G-Rust's equivalents live in `src/extension/parsers/{incoming,outgoing,subparsers}.rs` ([Sources](08-sources-and-repos.md)).
