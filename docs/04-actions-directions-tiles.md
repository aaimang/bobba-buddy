# Actions, directions, expressions and tiles

[← Contents](MAIN_CONTENT.md)

Value tables for the avatar packets in the [packet reference](03-packet-reference.md#avatar-actions), plus how rooms and tiles work.

## Dances: `{out:Dance}{i:style}`

| Value | Name | Needs HC |
|---|---|---|
| 0 | NONE (stop dancing) | no |
| 1 | NORMAL | no |
| 2 | POGOMOGO | yes |
| 3 | DUCKFUNK | yes |
| 4 | THEROLLIE | yes |

The bot only uses 0 and 1 unless `hc_dances = true`. Without it, a copied HC dance is sent as 1.

## Expressions: `{out:AvatarExpression}{i:n}`

| Value | Name | Needs HC | Bot behaviour |
|---|---|---|---|
| 1 | WAVE | no | used; also after every autopilot walk (`wave_on_walk`) and as the anti-idle wake-up |
| 2 | BLOW (blow a kiss) | yes | only with `hc_expressions = true` |
| 3 | LAUGH | yes | only with `hc_expressions = true` |
| 5 | IDLE | — | **never sent**; the client's own idle report is blocked by anti-idle |
| 6 | JUMP | yes | only with `hc_expressions = true` |
| 7 | THUMBSUP | no | used |

## Signs: `{out:Sign}{i:n}`

| Value | Sign | Value | Sign |
|---|---|---|---|
| 0–10 | the numbers 0 to 10 | 14 | SOCCERBALL |
| 11 | HEART | 15 | SMILE |
| 12 | SKULL | 16 | REDCARD |
| 13 | EXCLAMATION | 17 | YELLOWCARD |

A sign shows for a few seconds. While it's up, the holder's `UserUpdate` status contains `/sign N/`.

## Posture: `{out:ChangePosture}{i:n}`

| Value | Name |
|---|---|
| 0 | STAND |
| 1 | SIT (on the floor) |

Sitting fails while walking, so the bot skips it if the owner's status has `mv`. Sitting on a chair happens by walking onto the chair, not through this packet.

## Directions

Avatars face one of 8 directions. The numbering is the same in `Users` (`direction`), `UserUpdate` (`headDir`, `bodyDir`) and G-Python's `HDirection`:

```
        7 NW   0 N    1 NE
            ╲   │   ╱
     6 W  ──  avatar  ──  2 E
            ╱   │   ╲
        5 SW   4 S    3 SE
```

The compass names are the enum's labels. How they look on screen depends on Habbo's isometric camera, so check in-game before relying on a specific screen direction.

## `LookTo` directions

`{out:LookTo}{i:x}{i:y}` turns the avatar to face tile (x, y). Picking a point far outside any room makes the avatar face the same way wherever it stands. These are the targets found in-game; the autopilot's look action picks one at random (`LOOK_DIRECTIONS` in `protocol.py`):

| Expression | Faces |
|---|---|
| `{out:LookTo}{i:-9999}{i:-9999}` | direction 0 |
| `{out:LookTo}{i:-500}{i:500}` | (unlabelled in testing) |
| `{out:LookTo}{i:500}{i:0}` | direction 2 |
| `{out:LookTo}{i:9999}{i:9999}` | direction 3 |
| `{out:LookTo}{i:5000}{i:9999}` | direction 4 |
| `{out:LookTo}{i:0}{i:9999}` | direction 5 |
| `{out:LookTo}{i:-9999}{i:9999}` | direction 6 |
| `{out:LookTo}{i:-9999}{i:0}` | direction 7 |

## Tiles

A tile is an (x, y) grid position plus a height z. G-Python models it as `HPoint(x, y, z)`; the bot uses plain `(x, y)` tuples.

- **Where tiles come from:**
  - `Users` gives each entity's starting x, y, z.
  - `UserUpdate` gives the current x, y, z, plus the walk target in `/mv x,y,z/`.
  - z is sent as a string (for example `"0.0"` or `"5.32"`). It's above zero on furniture or raised floor.
- **Distance:** the bot uses Chebyshev distance, `max(|dx|, |dy|)`, so "within 1 tile" means the 8 surrounding tiles. That's `chebyshev()` in `room.py`, used for copying (`mimic_radius`) and walking (`walk_radius`).

### The floor map: `FloorHeightMap`

The `rows` string describes the room floor, one row per line, separated by `\r`:

```
xxxxxxxxxxxx
xxxx00000000
xxxx00000000
xxx000000000     ← the 0 sticking out on the left is the door
xxxx00000000
```

- **`x`:** not floor (wall or void).
- **`0`–`9`, then `a`–`z`:** floor at that height level.
- **Coordinates:** the column is x and the row is y.

`parse_floor_heightmap()` turns this into a set of floor tiles.

### `HeightMap` (not used by the bot)

`HeightMap` encodes every tile as a short, according to G-Rust:
- **-1:** not a room tile.
- **Otherwise:** height = `(value & 16383) / 256`, and bit `16384` means "stacking blocked".

This includes furniture heights, unlike `FloorHeightMap`.

### The door

Walking onto the door tile makes you leave the room, so the bot never chooses it:

1. **`RoomEntryTile`** (`i x, i y, i dir`) gives the door directly.
2. **As a backup,** any floor tile with at most one floor neighbour (up, down, left or right) is treated as a dead end and skipped. That's what a door sticking out of the wall looks like.

### Where the autopilot walks

`RoomState.walk_targets(radius)` returns floor tiles that are:
- not the door, and not a dead end
- not occupied by anyone (including pets and bots)
- not the owner's own tile
- within `walk_radius` tiles of the owner (0 = anywhere)

Tiles covered by furniture still count as floor. If one is picked, the server's pathfinding stops next to the furniture instead.
