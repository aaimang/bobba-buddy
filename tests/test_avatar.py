"""Autopilot, mimic and anti-idle, driven step by step with a fake clock and seeded RNG."""

from __future__ import annotations

import random

import pytest
from conftest import (
    TO_CLIENT,
    TO_SERVER,
    FakeExtension,
    FakeResponder,
    InlineExecutor,
    floor_map,
    packet,
    pet,
    player,
    user_update,
    users_packet,
)

from bobba_buddy.bot import BobbaBuddy
from bobba_buddy.budget import Budget
from bobba_buddy.config import parse_config
from bobba_buddy.protocol import (
    parse_entry_tile,
    parse_floor_heightmap,
    parse_sleep,
    parse_user_updates,
)
from bobba_buddy.room import chebyshev

OWNER_IDX, FRIEND_IDX, FAR_IDX, PET_IDX = 0, 1, 2, 3
OWNER_POS, FRIEND_POS, FAR_POS, PET_POS = (5, 5), (6, 5), (9, 9), (5, 6)
DOOR = (0, 5)
# 10x10 room; column 0 is wall except the door tile sticking out at (0, 5).
ROWS = ["x" + "0" * 9 if y != 5 else "0" * 10 for y in range(10)]

MANUAL = {  # what the owner sends by hand to "unlock" each action
    "MoveAvatar": (4, 4),
    "Dance": (0,),
    "ChangePosture": (0,),
    "AvatarExpression": (1,),
    "Sign": (0,),
    "LookTo": (5, 4),
}


class Clock:
    """A settable fake clock.

    Attributes:
        now: The time :meth:`__call__` returns; advance it by hand.
    """

    def __init__(self) -> None:
        """Start at 1000 s."""
        self.now = 1000.0

    def __call__(self) -> float:
        """Return the current fake time.

        Returns:
            :attr:`now`.
        """
        return self.now


class AvatarHarness:
    """An BobbaBuddy in a 10x10 test room, driven step by step with a fake clock.

    The owner stands at (5, 5), a friend 1 tile away, another player far away, and a
    pet next to the owner. The door sticks out of the wall at (0, 5).

    Attributes:
        clock: The fake clock.
        config: The parsed test config.
        ext: The fake extension.
        bot: The bot under test (avatar thread not started).
    """

    def __init__(self, tmp_path, avatar: dict | None = None, seed: int = 7) -> None:
        """Build the bot and load the test room.

        Args:
            tmp_path: pytest's temporary folder, for the budget file.
            avatar: ``[avatar]`` overrides.
            seed: Random seed, for repeatable autopilot choices.
        """
        self.clock = Clock()
        self.config = parse_config({"avatar": avatar or {}})
        self.ext = FakeExtension()
        self.bot = BobbaBuddy(
            self.ext,
            lambda: self.config,
            FakeResponder(),
            Budget(tmp_path / "b.json", limit_usd=lambda: 1),
            executor=InlineExecutor(),
            clock=self.clock,
            rng=random.Random(seed),
        )
        self.bot.install()
        self.bot.start(avatar_thread=False)
        self.ext.event("connection_start")
        self.ext.fire(TO_CLIENT, "UserObject", 100, "Owner", "fig", "M")
        self.ext.fire(
            TO_CLIENT,
            "Users",
            pkt=users_packet(
                player(100, "Owner", OWNER_IDX, x=OWNER_POS[0], y=OWNER_POS[1]),
                player(101, "Friend", FRIEND_IDX, x=FRIEND_POS[0], y=FRIEND_POS[1]),
                player(102, "Far", FAR_IDX, x=FAR_POS[0], y=FAR_POS[1]),
                pet(103, "Rex", PET_IDX),
            ),
        )
        self.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((PET_IDX, *PET_POS, "/")))
        self.ext.fire(TO_CLIENT, "FloorHeightMap", pkt=floor_map(ROWS))
        self.ext.fire(TO_CLIENT, "RoomEntryTile", *DOOR, 2)
        self.ext.sent.clear()

    @property
    def avatar(self):
        """AvatarController: The bot's avatar controller."""
        return self.bot.avatar

    def unlock(self, *names: str) -> None:
        """The owner does each action once by hand, then waits out the owner pause.

        Args:
            *names: Outgoing action packet names.
        """
        for name in names:
            self.ext.fire(TO_SERVER, name, *MANUAL[name])
        self.advance(self.config.avatar.owner_pause_s + 1)

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward.

        Args:
            seconds: How far.
        """
        self.clock.now += seconds

    def step(self):
        """Run one avatar worker tick.

        Returns:
            The action sent, or ``None``.
        """
        return self.avatar.step()

    def actions(self):
        """Avatar actions sent so far.

        Returns:
            ``(name, args)`` pairs.
        """
        return self.ext.sent_actions()

    def console(self) -> str:
        """Everything written to the G-Earth console.

        Returns:
            The console text.
        """
        return self.ext.console_text()

    def close(self) -> None:
        """Stop the bot."""
        self.bot.stop()


@pytest.fixture
def h(tmp_path):
    """Fixture: a harness with the autopilot off (copying and anti-idle only).

    Args:
        tmp_path: pytest's temporary folder.

    Yields:
        The harness.
    """
    harness = AvatarHarness(tmp_path, {"autopilot": False})
    yield harness
    harness.close()


# --- protocol -------------------------------------------------------------------------


def test_parse_user_updates():
    statuses, problem = parse_user_updates(
        user_update((1, 3, 4, "/flatctrl 4/sit 0.5 1/sign 7/"), (2, 1, 1, "/mv 2,1,0.0/"))
    )
    assert problem is None
    status, moving = statuses
    assert (status.index, status.x, status.y) == (1, 3, 4)
    assert status.sitting and status.sign == 7 and not status.moving
    assert moving.moving and not moving.sitting and moving.sign is None


def test_parse_user_updates_keeps_good_entries_of_a_bad_packet():
    pkt = user_update((1, 3, 4, "/"), (2, 5, 6, "/sit 0.5 1/"))
    pkt.bytearray = pkt.bytearray[:-5]  # cut into the second entry
    pkt.fix_length()
    statuses, problem = parse_user_updates(pkt)
    assert [s.index for s in statuses] == [1]
    assert problem.startswith("entry 1:")


def test_parse_user_updates_rejects_implausible_values():
    statuses, problem = parse_user_updates(user_update((1, 3, 4, "/"), (9, 999999, 4, "/")))
    assert [s.index for s in statuses] == [1]
    assert "implausible position" in problem
    _, problem = parse_user_updates(user_update((1, 3, 4, "high", 2, 2, "/")))
    assert "implausible height" in problem
    _, problem = parse_user_updates(user_update((1, 3, 4, "0.0", 2, 2, "sit")))
    assert "implausible status" in problem
    _, problem = parse_user_updates(packet(-5))
    assert "implausible count" in problem


# Captured from the live AIR client (MAC63-202610131318): two users, each entry has an
# extra int after the head/body directions that the public parsers don't know about.
LIVE_USER_UPDATE = bytes.fromhex(
    "00 00 00 5d 0b 2f 00 00 00 02 00 00 00 05 00 00 00 0d 00 00 00 17 00 03 30 2e 30 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 17 2f 66 6c 61 74 63 74 72 6c 20 34 2f 73 69 74 20 30 2e 35 "
    "35 20 31 2f 00 00 00 09 00 00 00 00 00 00 00 0a 00 03 30 2e 30 00 00 00 02 00 00 00 02 00 "
    "00 00 00 00 02 2f 2f"
)


def test_parse_live_user_update_with_extra_int():
    from bobba_buddy.gpython import HPacket

    statuses, problem = parse_user_updates(HPacket.from_bytes(LIVE_USER_UPDATE))
    assert problem is None
    first, second = statuses
    assert (first.index, first.x, first.y, first.sitting) == (5, 13, 23, True)
    assert first.actions["flatctrl"] == "4"
    assert (second.index, second.x, second.y, second.sitting) == (9, 0, 10, False)


@pytest.mark.parametrize("extra_ints", [0, 1])
def test_parse_user_updates_both_layouts(extra_ints):
    pkt = user_update((1, 3, 4, "/sit 0.5 1/"), (2, 5, 6, "/sign 4/"), extra_ints=extra_ints)
    statuses, problem = parse_user_updates(pkt)
    assert problem is None
    assert [(s.index, s.sitting, s.sign) for s in statuses] == [(1, True, None), (2, False, 4)]


def test_single_entry_keeps_its_status():
    """Regression: with the extra int unread, a lone entry silently lost its status."""
    statuses, problem = parse_user_updates(user_update((1, 3, 4, "/sign 7/")))
    assert problem is None and statuses[0].sign == 7


def test_parse_floor_heightmap():
    tiles = parse_floor_heightmap(floor_map(["xx0", "x00", "000"]))
    assert tiles == {(2, 0), (1, 1), (2, 1), (0, 2), (1, 2), (2, 2)}


def test_parse_sleep_and_entry_tile():
    assert parse_sleep(packet(4, True)) == (4, True)
    assert parse_entry_tile(packet(0, 5, 2)) == (0, 5)


# --- mimic ----------------------------------------------------------------------------


def test_copies_dance_of_adjacent_player(h):
    h.unlock("Dance")
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 3)
    assert h.step().reason == "mimic"
    assert h.actions() == [("Dance", (1,))]  # HC dance mapped to the free one


def test_hc_dances_copied_exactly(tmp_path):
    harness = AvatarHarness(tmp_path, {"autopilot": False, "hc_dances": True})
    harness.unlock("Dance")
    harness.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 3)
    harness.step()
    assert harness.actions() == [("Dance", (3,))]
    harness.close()


def test_stops_dancing_when_they_stop(h):
    h.unlock("Dance")
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    h.step()
    h.ext.fire(TO_CLIENT, "Dance", OWNER_IDX, 1)  # server confirms the owner is dancing
    h.advance(2)
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 0)
    h.step()
    assert h.actions() == [("Dance", (1,)), ("Dance", (0,))]


@pytest.mark.parametrize("index", [FAR_IDX, PET_IDX, OWNER_IDX])
def test_ignores_far_players_pets_and_self(h, index):
    h.unlock("Dance")
    h.ext.fire(TO_CLIENT, "Dance", index, 1)
    assert h.step() is None
    assert h.actions() == []


def test_hc_expressions_not_copied_without_hc(h):
    for expression in (2, 3, 6):
        h.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, expression)
    assert h.step() is None


def test_hc_expressions_copied_with_hc(tmp_path):
    harness = AvatarHarness(tmp_path, {"autopilot": False, "hc_expressions": True})
    harness.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, 3)
    harness.step()
    assert harness.actions() == [("AvatarExpression", (3,))]
    harness.close()


def test_autopilot_waves_after_walking(tmp_path):
    harness = AvatarHarness(
        tmp_path,
        {
            "walk_weight": 1,
            "sit_weight": 0,
            "dance_weight": 0,
            "expression_weight": 0,
            "sign_weight": 0,
            "look_weight": 0,
        },
    )
    harness.step()
    harness.advance(91)
    assert harness.step().packet == "MoveAvatar"
    assert harness.step() is None  # action gap
    harness.advance(1.5)
    assert harness.step().args == (1,)
    assert [n for n, _ in harness.actions()] == ["MoveAvatar", "AvatarExpression"]
    harness.close()


def test_no_wave_on_walk_when_disabled(tmp_path):
    harness = AvatarHarness(
        tmp_path,
        {
            "walk_weight": 1,
            "sit_weight": 0,
            "dance_weight": 0,
            "expression_weight": 0,
            "sign_weight": 0,
            "look_weight": 0,
            "wave_on_walk": False,
        },
    )
    harness.step()
    harness.advance(91)
    harness.step()
    harness.advance(2)
    harness.step()
    assert [n for n, _ in harness.actions()] == ["MoveAvatar"]
    harness.close()


def test_copies_expressions_but_not_idle(h):
    h.unlock("AvatarExpression")
    h.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, 5)  # idle
    assert h.step() is None
    h.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, 7)  # thumbs up
    h.step()
    assert h.actions() == [("AvatarExpression", (7,))]


def test_copies_sign_once(h):
    h.unlock("Sign")
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((FRIEND_IDX, *FRIEND_POS, "/sign 11/")))
    h.step()
    h.advance(2)
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((FRIEND_IDX, *FRIEND_POS, "/sign 11/")))
    assert h.step() is None
    assert h.actions() == [("Sign", (11,))]


def test_copies_sit_then_stand(h):
    h.unlock("ChangePosture")
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((FRIEND_IDX, *FRIEND_POS, "/sit 0.5 1/")))
    h.step()
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((OWNER_IDX, *OWNER_POS, "/sit 0.5 1/")))
    h.advance(2)
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((FRIEND_IDX, *FRIEND_POS, "/")))
    h.step()
    assert h.actions() == [("ChangePosture", (1,)), ("ChangePosture", (0,))]


def test_bad_user_update_is_logged_once_and_partly_used(h, caplog):
    h.unlock("Dance")
    pkt = user_update((FAR_IDX, 4, 4, "/"), (FRIEND_IDX, 6, 5, "/sit 0.5 1/"))
    pkt.bytearray = pkt.bytearray[:-5]
    pkt.fix_length()
    with caplog.at_level("DEBUG", logger="bobba_buddy.guard"):
        h.ext.fire(TO_CLIENT, "UserUpdate", pkt=pkt)
        h.ext.fire(TO_CLIENT, "UserUpdate", pkt=pkt)
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1  # dumped once, then debug only
    assert "UserUpdate packet didn't match" in warnings[0].getMessage()
    assert "header=" in warnings[0].getMessage()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]
    # The good first entry still counts: Far is now next to the owner.
    h.ext.fire(TO_CLIENT, "Dance", FAR_IDX, 1)
    assert h.step() is not None


def test_follows_new_positions(h):
    h.unlock("Dance")
    h.ext.fire(TO_CLIENT, "UserUpdate", pkt=user_update((FAR_IDX, 4, 4, "/")))  # walks over
    h.ext.fire(TO_CLIENT, "Dance", FAR_IDX, 1)
    h.step()
    assert h.actions() == [("Dance", (1,))]


def test_action_gap(h):
    h.unlock("Dance", "AvatarExpression")
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    h.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, 1)
    assert h.step() is not None
    assert h.step() is None  # too soon
    h.advance(1.5)
    assert h.step() is not None
    assert [name for name, _ in h.actions()] == ["Dance", "AvatarExpression"]


def test_stale_copies_are_dropped(h):
    h.unlock("Dance")
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    h.advance(10)
    assert h.step() is None


@pytest.fixture
def strict(tmp_path):
    """Fixture: ``require_seen_packets = true``; actions wait until the owner did them once.

    Args:
        tmp_path: pytest's temporary folder.

    Yields:
        The harness.
    """
    harness = AvatarHarness(tmp_path, {"autopilot": False, "require_seen_packets": True})
    yield harness
    harness.close()


def test_strict_needs_manual_unlock(strict):
    strict.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    assert strict.step() is None
    assert "dance once by hand" in strict.console()


def test_copies_by_default_without_manual_unlock(h):
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    h.step()
    assert h.actions() == [("Dance", (1,))]


def test_mimic_disabled(tmp_path):
    harness = AvatarHarness(tmp_path, {"autopilot": False, "mimic_dance": False})
    harness.unlock("Dance")
    harness.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    assert harness.step() is None
    harness.close()


def test_bot_off_does_nothing(h):
    h.unlock("Dance")
    h.ext.event("double_click")  # OFF
    h.ext.fire(TO_CLIENT, "Dance", FRIEND_IDX, 1)
    assert h.step() is None


# --- anti-idle ------------------------------------------------------------------------


def test_blocks_client_idle_report(h):
    msg = h.ext.fire(TO_SERVER, "AvatarExpression", 5)
    assert msg.is_blocked
    wave = h.ext.fire(TO_SERVER, "AvatarExpression", 1)
    assert not wave.is_blocked


def test_idle_report_passes_when_anti_idle_off(tmp_path):
    harness = AvatarHarness(tmp_path, {"autopilot": False, "anti_idle": False})
    assert not harness.ext.fire(TO_SERVER, "AvatarExpression", 5).is_blocked
    harness.close()


def test_wakes_up_with_wave(h):
    h.unlock("AvatarExpression")
    h.ext.fire(TO_CLIENT, "Sleep", FAR_IDX, True)  # someone else
    assert h.step() is None
    h.ext.fire(TO_CLIENT, "Sleep", OWNER_IDX, True)
    assert h.step().reason == "wake"
    assert h.actions() == [("AvatarExpression", (1,))]


def test_strict_wakes_up_with_step_if_wave_not_unlocked(strict):
    strict.unlock("MoveAvatar")
    strict.ext.fire(TO_CLIENT, "Sleep", OWNER_IDX, True)
    strict.step()
    [(name, tile)] = strict.actions()
    assert name == "MoveAvatar"
    assert chebyshev(tile, OWNER_POS) == 1 and tile not in (FRIEND_POS, PET_POS)


def test_strict_wake_without_any_unlock_warns(strict):
    strict.ext.fire(TO_CLIENT, "Sleep", OWNER_IDX, True)
    assert strict.step() is None
    assert "wave once by hand" in strict.console()


def test_wakes_by_default_without_manual_unlock(h):
    h.ext.fire(TO_CLIENT, "Sleep", OWNER_IDX, True)
    h.step()
    assert h.actions() == [("AvatarExpression", (1,))]


# --- autopilot ------------------------------------------------------------------------


@pytest.fixture
def auto(tmp_path):
    """Fixture: a harness with the autopilot on.

    Args:
        tmp_path: pytest's temporary folder.

    Yields:
        The harness.
    """
    harness = AvatarHarness(tmp_path, {"autopilot": True})
    yield harness
    harness.close()


def run_autopilot(harness: AvatarHarness, rounds: int):
    """Advance past the autopilot interval ``rounds`` times, applying each action.

    Sits, stands and dances are echoed back as the server would, so the next choice
    sees the new state.

    Args:
        harness: The harness to drive.
        rounds: How many intervals to run.

    Returns:
        The actions sent, in order.
    """
    harness.step()  # first call only schedules
    done = []
    for _ in range(rounds):
        harness.advance(harness.config.avatar.autopilot_max_s + 1)
        action = harness.step()
        if action:
            done.append(action)
            if action.packet == "ChangePosture":  # pretend the server applied it
                status = "/sit 0.5 1/" if action.args == (1,) else "/"
                harness.ext.fire(
                    TO_CLIENT, "UserUpdate", pkt=user_update((OWNER_IDX, *OWNER_POS, status))
                )
            if action.packet == "Dance":
                harness.ext.fire(TO_CLIENT, "Dance", OWNER_IDX, action.args[0])
    return done


def test_autopilot_mixes_all_actions(auto):
    done = run_autopilot(auto, 150)
    assert {a.packet for a in done} == {
        "MoveAvatar",
        "ChangePosture",
        "Dance",
        "AvatarExpression",
        "Sign",
        "LookTo",
    }
    assert all(a.reason == "auto" for a in done)
    from bobba_buddy.protocol import LOOK_DIRECTIONS

    for a in done:
        if a.packet == "AvatarExpression":
            assert a.args[0] in {1, 7}  # no HC expressions, never idle (5)
        elif a.packet == "Sign":
            assert 0 <= a.args[0] <= 17
        elif a.packet == "LookTo":
            assert a.args in LOOK_DIRECTIONS


def test_autopilot_weights_zero_disable_actions(tmp_path):
    harness = AvatarHarness(
        tmp_path,
        {
            "walk_weight": 0,
            "sit_weight": 0,
            "dance_weight": 0,
            "look_weight": 0,
            "expression_weight": 0,
            "sign_weight": 1,
        },
    )
    done = run_autopilot(harness, 10)
    assert done and {a.packet for a in done} == {"Sign"}
    harness.close()


def test_autopilot_walk_targets_are_safe(auto):
    auto.unlock("MoveAvatar")
    walks = [a.args for a in run_autopilot(auto, 40) if a.packet == "MoveAvatar"]
    assert walks
    for tile in walks:
        assert chebyshev(tile, OWNER_POS) <= 5
        assert tile not in (DOOR, OWNER_POS, FRIEND_POS, FAR_POS, PET_POS)
        assert tile[0] >= 1  # never the wall column


def test_door_excluded_without_entry_tile():
    from bobba_buddy.protocol import RoomUser, UserKind
    from bobba_buddy.room import RoomState

    room = RoomState()
    room.set_identity(1, "Owner")
    room.add_users([RoomUser(1, "Owner", "", 0, UserKind.PLAYER, 1, 5)])
    room.set_floor(parse_floor_heightmap(floor_map(ROWS)))
    assert DOOR not in room.walk_targets(0)


def test_autopilot_waits_for_interval(auto):
    auto.unlock("Dance")
    auto.step()  # schedules the first run 30-90 s out
    auto.advance(auto.config.avatar.autopilot_min_s - 1)
    assert auto.step() is None


def test_autopilot_pauses_when_owner_acts(auto):
    auto.unlock("MoveAvatar", "Dance")
    auto.step()
    auto.advance(91)
    auto.ext.fire(TO_SERVER, "MoveAvatar", 7, 7)  # owner walks by hand
    assert auto.step() is None
    auto.advance(61)
    assert auto.step() is not None


def test_autopilot_pauses_after_copying(auto):
    auto.unlock("Dance", "AvatarExpression")
    auto.step()
    auto.advance(91)  # autopilot is due, but a copy takes priority
    auto.ext.fire(TO_CLIENT, "Expression", FRIEND_IDX, 1)
    assert auto.step().reason == "mimic"
    auto.advance(2)
    assert auto.step() is None  # 30 s mimic pause
    auto.advance(30)
    assert auto.step().reason == "auto"


def test_own_sends_dont_count_as_owner_activity(auto):
    auto.unlock("MoveAvatar")
    walk = run_autopilot(auto, 5)[0]
    auto.ext.fire(TO_SERVER, "MoveAvatar", *walk.args)  # G-Earth echoes our own packet
    auto.advance(91)
    assert auto.step() is not None


def test_strict_autopilot_waiting_notice(tmp_path):
    harness = AvatarHarness(tmp_path, {"autopilot": True, "require_seen_packets": True})
    run_autopilot(harness, 2)
    assert "Autopilot is waiting" in harness.console()
    harness.close()


def test_autopilot_acts_by_default_without_manual_unlock(auto):
    done = run_autopilot(auto, 10)
    assert done and all(a.reason == "auto" for a in done)


def test_room_change_resets(auto):
    auto.unlock("MoveAvatar")
    auto.ext.fire(TO_CLIENT, "RoomReady", "model_a", 5)
    assert auto.bot.room.walk_targets(5) == []
    assert run_autopilot(auto, 3) == []


def test_strict_startup_notice_lists_manual_actions(tmp_path):
    harness = AvatarHarness(tmp_path, {"require_seen_packets": True})
    text = harness.console()
    assert "autopilot, copying, anti-idle on" in text
    assert "walk, sit, dance, wave, hold up a sign" in text
    harness.close()


def test_startup_notice_by_default(auto):
    text = auto.console()
    assert "Avatar: autopilot, copying, anti-idle on." in text
    assert "once by hand so G-Earth lets the bot" not in text
    assert "Developer mode" in text


def test_invalid_avatar_config():
    from bobba_buddy.config import ConfigError

    for bad in (
        {"autopilot_min_s": 2},
        {"autopilot_min_s": 60, "autopilot_max_s": 30},
        {"mimic_radius": 0},
        {"wake_actions": ["dance"]},
        {"action_gap_s": 0.1},
    ):
        with pytest.raises(ConfigError):
            parse_config({"avatar": bad})
