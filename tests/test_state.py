"""Access control, budget, and memory."""

import json
from datetime import date
from types import SimpleNamespace

from bobba_buddy.access import AccessControl, Denial, DenyReason, Ticket, is_allowed
from bobba_buddy.budget import Budget, estimate_cost
from bobba_buddy.config import PricingConfig
from bobba_buddy.memory import ConversationMemory


class Clock:
    """A settable fake clock.

    Attributes:
        now: The time :meth:`__call__` returns; advance it by hand.
    """

    def __init__(self):
        """Start at 1000 s."""
        self.now = 1000.0

    def __call__(self):
        """Return the current fake time.

        Returns:
            :attr:`now`.
        """
        return self.now


class TestAccess:
    def test_whitelist(self):
        wl = frozenset({"ali"})
        assert is_allowed("ALI", wl, None)
        assert is_allowed("Owner", wl, "owner")
        assert not is_allowed("Bob", wl, "Owner")

    def test_cooldown_and_in_flight(self):
        clock = Clock()
        access = AccessControl(clock)
        ticket = access.acquire("Ali", "q", 15)
        assert isinstance(ticket, Ticket)
        assert access.acquire("ali", "joke", 15) == Denial(DenyReason.IN_FLIGHT)
        access.release(ticket)
        denial = access.acquire("Ali", "q", 15)
        assert isinstance(denial, Denial) and denial.reason is DenyReason.COOLDOWN
        assert isinstance(access.acquire("Ali", "joke", 15), Ticket)  # per-command cooldowns

    def test_cooldown_expires(self):
        clock = Clock()
        access = AccessControl(clock)
        access.release(access.acquire("Ali", "q", 15))
        clock.now += 15
        assert isinstance(access.acquire("Ali", "q", 15), Ticket)

    def test_refund_restores_cooldowns(self):
        clock = Clock()
        access = AccessControl(clock)
        ticket = access.acquire("Ali", "roast", 15, target="Bob", target_cooldown_s=60)
        access.release(ticket, refund=True)
        assert isinstance(
            access.acquire("Ali", "roast", 15, target="Bob", target_cooldown_s=60), Ticket
        )

    def test_target_cooldown_blocks_pile_on(self):
        clock = Clock()
        access = AccessControl(clock)
        access.release(access.acquire("Ali", "roast", 15, target="Bob", target_cooldown_s=60))
        denial = access.acquire("Cat", "roast", 15, target="BOB", target_cooldown_s=60)
        assert denial.reason is DenyReason.TARGET_COOLDOWN
        assert 59 <= denial.retry_in_s <= 60
        # The failed attempt still costs Cat their own cooldown.
        clock.now += 61
        assert isinstance(
            access.acquire("Cat", "roast", 15, target="Bob", target_cooldown_s=60), Ticket
        )


class TestBudget:
    def test_estimate_cost(self):
        usage = SimpleNamespace(
            input_tokens=1_000_000,
            output_tokens=200_000,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=0,
            server_tool_use=SimpleNamespace(web_search_requests=3),
        )
        assert round(estimate_cost(usage, PricingConfig()), 4) == round(0.10 + 0.10 + 0.03, 4)
        assert estimate_cost(None, PricingConfig()) == 0.0

    def test_cap_persists_and_resets_at_midnight(self, tmp_path):
        day = [date(2026, 10, 13)]
        path = tmp_path / "state" / "budget.json"
        budget = Budget(path, limit_usd=lambda: 0.05, today=lambda: day[0])
        assert budget.can_spend()
        budget.record(0.03)
        budget.record(0.03)
        assert not budget.can_spend()
        assert json.loads(path.read_text())["requests"] == 2

        reloaded = Budget(path, limit_usd=lambda: 0.05, today=lambda: day[0])
        assert not reloaded.can_spend()

        day[0] = date(2026, 10, 14)
        assert reloaded.can_spend()
        assert reloaded.spent_today == 0

    def test_zero_limit_means_uncapped(self, tmp_path):
        budget = Budget(tmp_path / "b.json", limit_usd=lambda: 0)
        budget.record(100.0)
        assert budget.can_spend()

    def test_first_denial_once_per_day(self, tmp_path):
        day = [date(2026, 10, 13)]
        budget = Budget(tmp_path / "b.json", limit_usd=lambda: 1, today=lambda: day[0])
        assert budget.first_denial_today("Ali")
        assert not budget.first_denial_today("ali")
        day[0] = date(2026, 10, 14)
        assert budget.first_denial_today("Ali")

    def test_corrupt_state_is_ignored(self, tmp_path):
        path = tmp_path / "b.json"
        path.write_text("{not json")
        assert Budget(path, limit_usd=lambda: 1).can_spend()


class TestMemory:
    def test_history_trim_and_ttl(self):
        clock = Clock()
        memory = ConversationMemory(clock)
        for i in range(4):
            memory.append("Ali", f"q{i}", f"a{i}", max_turns=2)
        history = memory.history("ALI", ttl_s=600)
        assert [m["content"] for m in history] == ["q2", "a2", "q3", "a3"]
        assert history[0]["role"] == "user" and history[1]["role"] == "assistant"
        clock.now += 601
        assert memory.history("Ali", ttl_s=600) == []

    def test_reset_and_disabled(self):
        memory = ConversationMemory(Clock())
        memory.append("Ali", "q", "a", max_turns=6)
        memory.reset("ali")
        assert memory.history("Ali", 600) == []
        memory.append("Ali", "q", "a", max_turns=0)
        assert memory.history("Ali", 600) == []
