"""End-to-end behaviour against a fake G-Earth extension and a scripted responder."""

from __future__ import annotations

import time
from datetime import date

import pytest
from conftest import (
    TO_CLIENT,
    TO_SERVER,
    FakeExtension,
    FakeResponder,
    InlineExecutor,
    incoming_chat,
    pet,
    player,
    users_packet,
)

from bobba_buddy.bot import MSG_BUDGET, MSG_BUSY, BobbaBuddy
from bobba_buddy.budget import Budget
from bobba_buddy.config import parse_config
from bobba_buddy.llm import Reply, ReplyKind
from bobba_buddy.protocol import ChatMode

OWNER, FRIEND, STRANGER = "Owner", "Friend", "Stranger"
OWNER_IDX, FRIEND_IDX, STRANGER_IDX, PET_IDX = 0, 1, 2, 3


class Harness:
    """An BobbaBuddy in a test room with the owner, a whitelisted friend, a stranger and a pet.

    Attributes:
        config: The parsed test config.
        ext: The fake extension.
        responder: The scripted responder.
        budget: The budget tracker.
        bot: The bot under test.
    """

    def __init__(
        self,
        tmp_path,
        config: dict | None = None,
        responder: FakeResponder | None = None,
        budget_usd: float = 1.0,
    ):
        """Build the bot and load the test room.

        Args:
            tmp_path: pytest's temporary folder, for the budget file.
            config: Config overrides, merged per section.
            responder: Responder to use; a default :class:`FakeResponder` if ``None``.
            budget_usd: Daily budget.
        """
        base = {"whitelist": [FRIEND], "chat": {"send_interval_s": 0.0}}
        for key, value in (config or {}).items():
            base[key] = {**base.get(key, {}), **value} if isinstance(value, dict) else value
        self.config = parse_config(base)
        self.ext = FakeExtension()
        self.responder = responder or FakeResponder()
        self.budget = Budget(
            tmp_path / "budget.json", limit_usd=lambda: budget_usd, today=lambda: date(2026, 10, 13)
        )
        self.bot = BobbaBuddy(
            self.ext,
            lambda: self.config,
            self.responder,
            self.budget,
            executor=InlineExecutor(),
            today=lambda: date(2026, 10, 13),
        )
        self.bot.install()
        self.bot.start(avatar_thread=False)
        self.ext.event("connection_start")
        self.ext.fire(TO_CLIENT, "UserObject", 100, OWNER, "fig", "M")
        self.ext.fire(
            TO_CLIENT,
            "Users",
            pkt=users_packet(
                player(100, OWNER, OWNER_IDX),
                player(101, FRIEND, FRIEND_IDX, motto="pixel hoarder"),
                player(102, STRANGER, STRANGER_IDX),
                pet(103, "Rex", PET_IDX),
            ),
        )

    def owner_says(self, text: str, mode: ChatMode = ChatMode.CHAT, target: str = FRIEND):
        """Simulate the owner's client sending chat.

        Args:
            text: The message.
            mode: Chat, shout or whisper.
            target: Whisper target.
        """
        if mode is ChatMode.CHAT:
            self.ext.fire(TO_SERVER, "Chat", text, 0, 7)
        elif mode is ChatMode.SHOUT:
            self.ext.fire(TO_SERVER, "Shout", text, 0)
        else:
            self.ext.fire(TO_SERVER, "Whisper", f"{target} {text}", 0)

    def user_says(self, index: int, text: str, mode: ChatMode = ChatMode.CHAT):
        """Simulate another user's chat arriving.

        Args:
            index: Speaker's room index.
            text: The message.
            mode: Chat, shout or whisper.
        """
        self.ext.fire(TO_CLIENT, mode.packet_name, *incoming_chat(index, text))

    def sent(self):
        """Wait for the outbox to empty, then return what was sent.

        Returns:
            The decoded chat packets.
        """
        assert self.bot.outbox.wait_idle(2.0)
        return self.ext.sent_chat()

    def close(self):
        """Stop the bot."""
        self.bot.stop()


@pytest.fixture
def harness(tmp_path):
    """Fixture: a default harness where the owner has already chatted once.

    Args:
        tmp_path: pytest's temporary folder.

    Yields:
        The harness.
    """
    h = Harness(tmp_path)
    h.owner_says("hello everyone")  # G-Earth has now seen an outgoing Chat
    yield h
    h.close()


def test_friend_chat_gets_chat_reply(harness):
    harness.user_says(FRIEND_IDX, "/q what's the capital of Malaysia?")
    sent = harness.sent()
    assert [s.mode for s in sent] == [ChatMode.CHAT]
    assert sent[0].text == "Kuala Lumpur is the capital of Malaysia."
    assert sent[0].tail == 7  # copied from the owner's own Chat packet
    call = harness.responder.calls[0]
    assert call["web_search"] is True
    assert call["messages"][-1]["content"].startswith("[from Friend | 2026-10-13]")


def test_long_answer_is_split_into_bubbles(harness):
    harness.responder.default_text = "word " * 80
    harness.user_says(FRIEND_IDX, "/q tell me a lot")
    sent = harness.sent()
    assert len(sent) == 3
    assert all(len(s.text) <= 100 for s in sent)
    assert sent[-1].text.endswith("…")


def test_owner_whisper_is_answered_by_whisper(harness):
    harness.owner_says("/q hi", ChatMode.WHISPER, target=FRIEND)
    sent = harness.sent()
    assert [(s.mode, s.whisper_target) for s in sent] == [(ChatMode.WHISPER, FRIEND)]
    assert len(f"{FRIEND} {sent[0].text}") <= 100


@pytest.fixture
def strict(tmp_path):
    """Fixture: ``require_seen_packets = true``; only reply modes the owner has used.

    Args:
        tmp_path: pytest's temporary folder.

    Yields:
        The harness.
    """
    h = Harness(tmp_path, {"chat": {"require_seen_packets": True}})
    h.owner_says("hello everyone")
    yield h
    h.close()


def test_strict_whisper_without_seen_whisper_is_not_sent_publicly(strict):
    strict.user_says(FRIEND_IDX, "/q secret?", ChatMode.WHISPER)
    assert strict.sent() == []
    assert strict.responder.calls == []  # no API call wasted
    assert "whisper anyone once" in strict.ext.console_text()


def test_whisper_sent_by_default_without_seeing_one(harness):
    harness.user_says(FRIEND_IDX, "/q secret?", ChatMode.WHISPER)
    assert [(s.mode, s.whisper_target) for s in harness.sent()] == [(ChatMode.WHISPER, FRIEND)]


def test_friend_whisper_after_owner_whispered(harness):
    harness.owner_says("hey", ChatMode.WHISPER, target=STRANGER)
    harness.user_says(FRIEND_IDX, "/q secret?", ChatMode.WHISPER)
    sent = harness.sent()
    assert [(s.mode, s.whisper_target) for s in sent] == [(ChatMode.WHISPER, FRIEND)]


def test_strict_shout_falls_back_to_chat_until_seen(strict):
    strict.user_says(FRIEND_IDX, "/joke", ChatMode.SHOUT)
    assert [s.mode for s in strict.sent()] == [ChatMode.CHAT]
    strict.owner_says("WOO", ChatMode.SHOUT)
    strict.bot.access.clear()
    strict.user_says(FRIEND_IDX, "/joke", ChatMode.SHOUT)
    assert [s.mode for s in strict.sent()][-1] == ChatMode.SHOUT


def test_shout_mirrored_by_default(harness):
    harness.user_says(FRIEND_IDX, "/joke", ChatMode.SHOUT)
    assert [s.mode for s in harness.sent()] == [ChatMode.SHOUT]


def test_strict_nothing_sent_before_owner_has_chatted(tmp_path):
    h = Harness(tmp_path, {"chat": {"require_seen_packets": True}})
    try:
        h.user_says(FRIEND_IDX, "/q hi")
        assert h.sent() == [] and h.responder.calls == []
        assert "say anything in chat once" in h.ext.console_text()
    finally:
        h.close()


def test_replies_by_default_before_owner_has_chatted(tmp_path):
    h = Harness(tmp_path)
    try:
        h.user_says(FRIEND_IDX, "/q hi")
        assert len(h.sent()) == 1
        assert "Developer mode" in h.ext.console_text()
    finally:
        h.close()


def test_non_whitelisted_user_is_ignored(harness):
    harness.user_says(STRANGER_IDX, "/q hi")
    assert harness.sent() == [] and harness.responder.calls == []


def test_pets_and_unknown_users_are_ignored(harness):
    harness.user_says(PET_IDX, "/q hi")
    harness.user_says(42, "/q hi")
    assert harness.responder.calls == []
    assert "Re-enter the room" in harness.ext.console_text()


def test_owner_echo_is_not_answered_twice(harness):
    harness.owner_says("/q hi")
    harness.user_says(OWNER_IDX, "/q hi")  # server echo of the owner's own chat
    assert len(harness.responder.calls) == 1


def test_ordinary_chat_is_ignored(harness):
    harness.user_says(FRIEND_IDX, "hello there")
    assert harness.responder.calls == []


def test_cooldown(harness):
    harness.user_says(FRIEND_IDX, "/q one")
    harness.user_says(FRIEND_IDX, "/q two")
    assert len(harness.responder.calls) == 1


def test_usage_and_reset(harness):
    harness.user_says(FRIEND_IDX, "/q")
    assert harness.sent()[0].text.startswith("Try: /q <question>")
    harness.user_says(FRIEND_IDX, "/q what is 2+2")
    assert harness.bot.memory.history(FRIEND, 600)
    harness.bot.access.clear()
    harness.user_says(FRIEND_IDX, "/q reset")
    assert harness.bot.memory.history(FRIEND, 600) == []


def test_q_uses_memory_other_commands_do_not(harness):
    harness.user_says(FRIEND_IDX, "/q first")
    harness.bot.access.clear()
    harness.user_says(FRIEND_IDX, "/q and why?")
    assert len(harness.responder.calls[1]["messages"]) == 3
    harness.bot.access.clear()
    harness.user_says(FRIEND_IDX, "/suggest cafe")
    assert len(harness.responder.calls[2]["messages"]) == 1
    assert harness.responder.calls[2]["web_search"] is False


class TestRoast:
    def test_roast_target_in_room(self, harness):
        harness.owner_says("/roast friend")
        call = harness.responder.calls[0]
        assert 'Target name: "Friend"' in call["messages"][-1]["content"]
        assert 'Target motto: "pixel hoarder"' in call["messages"][-1]["content"]
        assert "roast" in call["system"].lower()

    def test_bare_roast_targets_asker(self, harness):
        harness.user_says(FRIEND_IDX, "/roast")
        assert 'Target name: "Friend"' in harness.responder.calls[0]["messages"][-1]["content"]

    def test_absent_target(self, harness):
        harness.owner_says("/roast Ghost")
        assert harness.responder.calls == []
        assert harness.sent()[-1].text == "I can't see Ghost here."

    def test_opt_out(self, tmp_path):
        h = Harness(tmp_path, {"commands": {"roast": {"opt_out": ["friend"]}}})
        try:
            h.owner_says("hi")
            h.owner_says("/roast Friend")
            assert h.responder.calls == []
            assert h.sent()[-1].text == "Friend opted out of roasts."
        finally:
            h.close()

    def test_target_cooldown(self, harness):
        harness.owner_says("/roast Friend")
        harness.user_says(FRIEND_IDX, "/roast Owner")  # different target is fine
        harness.user_says(FRIEND_IDX, "/joke")
        harness.bot.access._last_use.clear()  # ignore per-user cooldowns for this check
        harness.user_says(FRIEND_IDX, "/roast Friend")
        assert len(harness.responder.calls) == 3
        assert harness.sent()[-1].text.startswith("Give Friend a break")


def test_busy_reply_and_refund(harness):
    harness.responder.replies.append(Reply(ReplyKind.BUSY, error="429"))
    harness.user_says(FRIEND_IDX, "/q hi")
    assert harness.sent()[-1].text == MSG_BUSY
    harness.user_says(FRIEND_IDX, "/q hi")  # cooldown was refunded
    assert len(harness.responder.calls) == 2


def test_fatal_error_pauses_until_switched_on(harness):
    harness.responder.replies.append(Reply(ReplyKind.FATAL, error="AuthenticationError"))
    harness.user_says(FRIEND_IDX, "/q hi")
    assert harness.bot.fatal_error
    assert "bot paused" in harness.ext.console_text()
    assert "switch the bot back ON" in harness.ext.console_text()
    harness.bot.access.clear()
    harness.user_says(FRIEND_IDX, "/q hi")
    assert len(harness.responder.calls) == 1
    assert harness.bot.status()["fatal_error"] == "AuthenticationError"
    harness.bot.set_enabled(True)  # the switch in the settings window clears the error
    assert harness.bot.status()["fatal_error"] is None
    harness.user_says(FRIEND_IDX, "/q hi")
    assert len(harness.responder.calls) == 2


def test_play_button_opens_settings(tmp_path):
    opened = []
    h = Harness(tmp_path)
    h.bot._open_settings = lambda: opened.append(True)
    h.ext.event("double_click")
    assert opened == [True]
    assert h.bot.enabled  # no longer toggles
    h.close()


def test_play_button_without_settings_window(harness):
    harness.ext.event("double_click")
    assert "settings window isn't available" in harness.ext.console_text()
    assert harness.bot.enabled


def test_set_enabled_and_status(harness):
    harness.bot.set_enabled(False)
    assert "Bot OFF" in harness.ext.console_text()
    harness.user_says(FRIEND_IDX, "/q hi")
    assert harness.responder.calls == []
    status = harness.bot.status()
    assert status["enabled"] is False
    assert status["own_name"] == OWNER
    assert status["spent_usd"] == 0.0
    assert status["budget_usd"] == harness.config.limits.daily_budget_usd
    harness.bot.set_enabled(True)
    assert "Bot ON" in harness.ext.console_text()


def test_daily_budget(tmp_path):
    h = Harness(tmp_path, budget_usd=0.0015)
    try:
        h.owner_says("hi")
        h.user_says(FRIEND_IDX, "/q one")  # costs 0.001
        h.user_says(FRIEND_IDX, "/joke")  # costs 0.001, now over the cap
        h.bot.access.clear()
        h.user_says(FRIEND_IDX, "/q three")
        h.bot.access.clear()
        h.user_says(FRIEND_IDX, "/q four")
        assert len(h.responder.calls) == 2
        texts = [s.text for s in h.sent()]
        assert texts.count(MSG_BUDGET) == 1  # told once per day
    finally:
        h.close()


def test_flood_control_pauses_outbox(harness):
    harness.ext.fire(TO_CLIENT, "FloodControl", 30)
    harness.user_says(FRIEND_IDX, "/q hi")
    time.sleep(0.1)
    assert harness.ext.sent_chat() == []
    assert "muted chat for 30s" in harness.ext.console_text()


def test_room_change_drops_pending_and_users(harness):
    harness.ext.fire(TO_CLIENT, "FloodControl", 30)
    harness.user_says(FRIEND_IDX, "/q hi")
    harness.ext.fire(TO_CLIENT, "RoomReady", "model_a", 5)
    assert harness.bot.room.user_count == 0
    assert harness.bot.outbox.wait_idle(1.0)
    assert harness.ext.sent_chat() == []


def test_users_with_an_odd_entry_still_load(harness, caplog):
    odd = player(200, "Newbie", 9, motto="hi") + [1, "undocumented"]
    with caplog.at_level("WARNING", logger="bobba_buddy.guard"):
        harness.ext.fire(TO_CLIENT, "Users", pkt=users_packet(odd, player(201, "Late", 10)))
    assert harness.bot.room.find_player("Newbie").index == 9
    assert harness.bot.room.find_player("Late").index == 10
    assert any("Users packet didn't match" in r.getMessage() for r in caplog.records)


def test_user_remove(harness):
    harness.ext.fire(TO_CLIENT, "UserRemove", str(FRIEND_IDX))
    harness.user_says(FRIEND_IDX, "/q hi")
    assert harness.responder.calls == []


def test_bad_packets_never_escape_handlers(harness):
    harness.ext.fire(TO_CLIENT, "Users", 5, "garbage")
    harness.ext.fire(TO_CLIENT, "Chat", 1)
    harness.ext.fire(TO_SERVER, "Whisper")
    harness.user_says(FRIEND_IDX, "/q still alive?")
    assert len(harness.sent()) == 1


def test_connection_start_requests_identity_and_users(harness):
    names = [p.incomplete_identifier for p in harness.ext.sent]
    assert "InfoRetrieve" in names and "GetHeightMap" in names
    assert "Ready." in harness.ext.console_text()


def test_missing_packet_names_are_reported(tmp_path):
    ext = FakeExtension()
    del ext.packet_infos[TO_CLIENT]["FloodControl"]
    del ext.packet_infos[TO_SERVER]["GetHeightMap"]
    cfg = parse_config({})
    bot = BobbaBuddy(
        ext,
        lambda: cfg,
        FakeResponder(),
        Budget(tmp_path / "b.json", limit_usd=lambda: 1),
        executor=InlineExecutor(),
    )
    bot.install()
    ext.event("connection_start")
    assert "FloodControl (in)" in ext.console_text()
    assert "GetHeightMap" not in [p.incomplete_identifier for p in ext.sent]


def test_bot_replies_are_not_reprocessed(harness):
    harness.responder.default_text = "/q loop?"
    harness.user_says(FRIEND_IDX, "/q hi")
    sent = harness.sent()
    assert sent[0].text == "q loop?"  # leading '/' stripped
    # If G-Earth echoes the bot's own packet back through the intercept, it's ignored.
    harness.owner_says(sent[0].text)
    assert len(harness.responder.calls) == 1


class TestRejoin:
    def make(self, tmp_path, rejoin=True, delay=0.0):
        """Build a bot configured for rejoining.

        Args:
            tmp_path: pytest's temporary folder.
            rejoin: ``rejoin_on_start``.
            delay: ``rejoin_delay_s``.

        Returns:
            The bot and its fake extension.
        """
        ext = FakeExtension()
        cfg = parse_config({"room": {"rejoin_on_start": rejoin, "rejoin_delay_s": delay}})
        bot = BobbaBuddy(
            ext,
            lambda: cfg,
            FakeResponder(),
            Budget(tmp_path / "b.json", limit_usd=lambda: 1),
            executor=InlineExecutor(),
            last_room_path=tmp_path / "state" / "last_room.json",
        )
        bot.install()
        return bot, ext

    @staticmethod
    def go_to_room_args(ext):
        """Arguments of every ``GetGuestRoom`` the bot sent.

        Args:
            ext: The fake extension.

        Returns:
            ``(room_id, entering, forward)`` per packet.
        """
        out = []
        for p in ext.sent:
            if p.incomplete_identifier == "GetGuestRoom":
                p.reset()
                out.append((p.read_int(), p.read_int(), p.read_int()))
        return out

    def test_room_ready_is_remembered(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 81234567)
        assert (tmp_path / "state" / "last_room.json").read_text() == '{"room_id": 81234567}'

    def test_rejoins_last_room_on_start(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 81234567)
        bot.room.new_room()  # bot restarted mid-room: nothing loaded
        assert bot.rejoin_last_room()
        assert self.go_to_room_args(ext) == [(81234567, 0, 1)]
        assert "Re-entering your last room" in ext.console_text()

    def test_skips_when_room_already_loaded(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 5)
        ext.fire(TO_CLIENT, "Users", pkt=users_packet(player(1, "A", 0)))
        assert not bot.rejoin_last_room()
        assert self.go_to_room_args(ext) == []

    def test_no_saved_room(self, tmp_path):
        bot, ext = self.make(tmp_path)
        assert not bot.rejoin_last_room()
        assert "No saved room" in ext.console_text()

    def test_timer_fires_after_connect(self, tmp_path):
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "last_room.json").write_text('{"room_id": 42}')
        bot, ext = self.make(tmp_path, delay=0.0)
        ext.event("connection_start")
        time.sleep(0.2)
        assert self.go_to_room_args(ext) == [(42, 0, 1)]
        bot.stop()

    def test_disabled_by_default(self, tmp_path):
        (tmp_path / "state").mkdir()
        (tmp_path / "state" / "last_room.json").write_text('{"room_id": 42}')
        bot, ext = self.make(tmp_path, rejoin=False)
        ext.event("connection_start")
        time.sleep(0.2)
        assert self.go_to_room_args(ext) == []
        bot.stop()


class TestReturnOnExit:
    def make(self, tmp_path, **room):
        """Build a bot configured to return after leaving a room.

        Args:
            tmp_path: pytest's temporary folder.
            **room: ``[room]`` overrides.

        Returns:
            The bot and its fake extension.
        """
        ext = FakeExtension()
        cfg = parse_config(
            {
                "room": {
                    "return_on_exit": True,
                    "return_delay_s": 0.0,
                    "return_max_attempts": 2,
                    **room,
                }
            }
        )
        bot = BobbaBuddy(
            ext,
            lambda: cfg,
            FakeResponder(),
            Budget(tmp_path / "b.json", limit_usd=lambda: 1),
            executor=InlineExecutor(),
            last_room_path=tmp_path / "state" / "last_room.json",
        )
        bot.install()
        return bot, ext

    @staticmethod
    def returns(ext):
        """Every ``GetGuestRoom`` the bot sent.

        Args:
            ext: The fake extension.

        Returns:
            The packets.
        """
        return [p for p in ext.sent if p.incomplete_identifier == "GetGuestRoom"]

    def test_goes_back_after_leaving(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 77)
        ext.fire(TO_CLIENT, "CloseConnection")
        time.sleep(0.2)
        assert len(self.returns(ext)) == 1
        assert "going back to 77" in ext.console_text()
        bot.stop()

    def test_moving_to_another_room_does_not_trigger(self, tmp_path):
        bot, ext = self.make(tmp_path, return_delay_s=0.3)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 77)
        ext.fire(TO_CLIENT, "CloseConnection")
        ext.fire(TO_CLIENT, "RoomReady", "model_b", 88)  # arrived in the next room
        time.sleep(0.5)
        assert self.returns(ext) == []
        bot.stop()

    def test_gives_up_after_max_attempts(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 77)
        for _ in range(4):  # each return fails: kicked straight back out
            ext.fire(TO_CLIENT, "CloseConnection")
            time.sleep(0.15)
        assert len(self.returns(ext)) == 2
        assert "last try of 2" in ext.console_text()
        bot.stop()

    def test_off_when_bot_off_or_disabled(self, tmp_path):
        bot, ext = self.make(tmp_path)
        ext.fire(TO_CLIENT, "RoomReady", "model_a", 77)
        bot.set_enabled(False)
        ext.fire(TO_CLIENT, "CloseConnection")
        time.sleep(0.2)
        assert self.returns(ext) == []
        bot.stop()

        bot2, ext2 = self.make(tmp_path, return_on_exit=False)
        ext2.fire(TO_CLIENT, "CloseConnection")
        time.sleep(0.2)
        assert self.returns(ext2) == []
        bot2.stop()


# --- hotel windows (/bb and notification bubbles) ----------------------------------


def hotel_windows(h: Harness):
    """Stop the notify thread, send what's queued, and decode what reached the client.

    Args:
        h: The harness.

    Returns:
        The decoded windows.
    """
    h.bot.hotel.stop()
    h.bot.hotel.flush()
    return h.ext.windows()


def bubbles(h: Harness) -> list[str]:
    return [p["message"] for name, (_, p) in hotel_windows(h) if name == "NotificationDialog"]


def popups(h: Harness) -> list[dict[str, str]]:
    return [p for name, (_, p) in hotel_windows(h) if p["display"] == "POP_UP"]


@pytest.mark.parametrize("mode", [ChatMode.CHAT, ChatMode.SHOUT])
def test_bb_help_is_blocked_and_opens_a_popup(harness, mode):
    text = "/bb help"
    if mode is ChatMode.CHAT:
        msg = harness.ext.fire(TO_SERVER, "Chat", text, 0, 7)
    else:
        msg = harness.ext.fire(TO_SERVER, "Shout", text, 0)
    assert msg.is_blocked
    [popup] = popups(harness)
    assert popup["title"].startswith("Bobba Buddy ")
    assert "<b>/q</b> &lt;question&gt;" in popup["message"]
    assert "<b>/q reset</b>" in popup["message"] and "<b>/bb status</b>" in popup["message"]
    assert popup["linkUrl"].startswith("https://aaimang.github.io/")
    assert harness.sent() == []  # nothing went to the room
    assert harness.responder.calls == []


def test_bb_status(harness):
    msg = harness.ext.fire(TO_SERVER, "Chat", "/BB status", 0, 7)
    assert msg.is_blocked
    [popup] = popups(harness)
    page = popup["message"]
    assert "<b>Bot:</b> <font color=" in page and ">ON</font>" in page
    assert "game-us.habbo.com" in page and "<b>Logged in as:</b> Owner" in page
    assert "4 in the room" in page and "1 name(s)" in page and "$0.00 of $1.00" in page


def test_bb_status_shows_problems_in_red(tmp_path):
    h = Harness(tmp_path)
    try:
        h.bot.fatal_error = "bad <key>"
        h.ext.fire(TO_CLIENT, "RoomReady", "model_a", 1)  # new room, user list empty
        h.ext.fire(TO_SERVER, "Chat", "/bb status", 0, 7)
        [popup] = popups(h)
        assert "PAUSED: bad &lt;key&gt;</font>" in popup["message"]
        assert "not loaded, re-enter the room</font>" in popup["message"]
    finally:
        h.close()


def test_bb_test_sends_a_bubble_and_a_popup(harness):
    harness.ext.fire(TO_SERVER, "Chat", "/bb test", 0, 7)
    assert [p["display"] for _, (_, p) in hotel_windows(harness)] == ["BUBBLE", "POP_UP"]


def test_bb_needs_a_word_boundary_and_is_owner_only(harness):
    msg = harness.ext.fire(TO_SERVER, "Chat", "/bbq tonight", 0, 7)
    assert not msg.is_blocked
    harness.user_says(FRIEND_IDX, "/bb status")
    assert hotel_windows(harness) == []


def test_bb_prefix_is_configurable(tmp_path):
    h = Harness(tmp_path, {"notify": {"prefix": "/me2"}})
    try:
        assert h.ext.fire(TO_SERVER, "Chat", "/me2", 0, 7).is_blocked
        assert not h.ext.fire(TO_SERVER, "Chat", "/bb", 0, 7).is_blocked
        assert [p["display"] for _, (_, p) in hotel_windows(h)] == ["POP_UP"]
    finally:
        h.close()


def test_cooldown_bubble_once_per_gap(harness):
    harness.user_says(FRIEND_IDX, "/q one")
    harness.user_says(FRIEND_IDX, "/q two")
    harness.user_says(FRIEND_IDX, "/q three")
    texts = bubbles(harness)
    assert len(texts) == 1
    assert texts[0].startswith("Bobba Buddy: Friend: /q on cooldown, ")
    assert len(harness.responder.calls) == 1


def test_cooldown_bubble_can_be_turned_off(tmp_path):
    h = Harness(tmp_path, {"notify": {"cooldowns": False}})
    try:
        h.user_says(FRIEND_IDX, "/q one")
        h.user_says(FRIEND_IDX, "/q two")
        assert bubbles(h) == []
    finally:
        h.close()


def test_nothing_in_the_hotel_when_switched_off(tmp_path):
    h = Harness(tmp_path, {"notify": {"in_hotel": False}})
    try:
        h.user_says(FRIEND_IDX, "/q one")
        h.user_says(FRIEND_IDX, "/q two")
        assert bubbles(h) == []
        assert h.ext.fire(TO_SERVER, "Chat", "/bb help", 0, 7).is_blocked  # still yours
    finally:
        h.close()


def test_unknown_user_asks_to_reenter_the_room(harness):
    harness.user_says(42, "/q who am I?")
    [text] = bubbles(harness)
    assert "Re-enter the room" in text


def test_budget_and_api_errors_bubble(tmp_path):
    h = Harness(
        tmp_path,
        responder=FakeResponder(replies=[Reply(ReplyKind.FATAL, "", error="invalid x-api-key")]),
    )
    try:
        h.user_says(FRIEND_IDX, "/q hi")
        [text] = bubbles(h)
        assert "Bot paused: invalid x-api-key" in text
    finally:
        h.close()


def test_connection_end_clears_the_queue(harness):
    harness.bot.hotel.stop()
    harness.user_says(FRIEND_IDX, "/q one")
    harness.user_says(FRIEND_IDX, "/q two")
    harness.ext.event("connection_end")
    harness.bot.hotel.flush()
    assert harness.ext.windows() == []
