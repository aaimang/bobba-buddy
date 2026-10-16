"""Sanitizing model output and splitting it into chat bubbles."""

import pytest

from habbo_ask_bot.textfmt import ELLIPSIS, sanitize, split_bubbles, ulen

LOREM = (
    "Habbo Hotel is a social game where you design rooms, collect furni, trade rares, play "
    "games with friends, and hang out in public spaces like the Habbo Lido and the cafe."
)


class TestSanitize:
    def test_flattens_markdown_and_drops_links(self):
        text = "**Hello** there!\n- see [the docs](https://x.com/docs)\n> quote `code` www.a.com/b"
        assert sanitize(text) == "Hello there! see the docs quote code"

    def test_strips_command_lead(self):
        assert sanitize("/q yourself") == "q yourself"
        assert sanitize(" :: /sit down") == "sit down"

    def test_keeps_underscores_and_numbering(self):
        assert sanitize("Cool_Guy says 1) foo 2) bar") == "Cool_Guy says 1) foo 2) bar"

    def test_removes_control_characters(self):
        assert sanitize("a\x00b\tc\r\nd") == "a b c d"


class TestSplitBubbles:
    @pytest.mark.parametrize("limit", [100, 60, 33])
    def test_every_bubble_fits(self, limit):
        for bubble in split_bubbles(LOREM * 2, limit, 10):
            assert ulen(bubble) <= limit

    def test_breaks_on_word_boundaries(self):
        bubbles = split_bubbles(LOREM, 100, 5)
        assert " ".join(bubbles) == LOREM
        assert all(b == b.strip() for b in bubbles)

    def test_short_text_is_one_bubble(self):
        assert split_bubbles("hi there", 100, 3) == ["hi there"]

    def test_hard_splits_overlong_words(self):
        bubbles = split_bubbles("x" * 250, 100, 5)
        assert [len(b) for b in bubbles] == [100, 100, 50]

    def test_truncates_with_ellipsis(self):
        bubbles = split_bubbles(LOREM * 3, 100, 2)
        assert len(bubbles) == 2
        assert bubbles[-1].endswith(ELLIPSIS)
        assert ulen(bubbles[-1]) <= 100

    def test_whisper_budget(self):
        limit = 100 - len("SomeFriend") - 1
        assert all(ulen(b) <= limit for b in split_bubbles(LOREM, limit, 3))

    def test_counts_emoji_as_two_units(self):
        bubbles = split_bubbles("😀" * 80, 100, 5)
        assert all(ulen(b) <= 100 for b in bubbles)
        assert len(bubbles) == 2

    def test_malay_text(self):
        text = "Kuala Lumpur ialah ibu negara Malaysia dan bandar terbesar di negara ini. " * 3
        bubbles = split_bubbles(text, 100, 3)
        assert all(ulen(b) <= 100 for b in bubbles)
        assert bubbles[0].startswith("Kuala Lumpur")

    def test_later_bubble_never_starts_with_command_char(self):
        text = "a" * 99 + " :) nice"
        bubbles = split_bubbles(text, 100, 3)
        assert all(not b.startswith((":", "/")) for b in bubbles)

    def test_rejects_bad_limits(self):
        with pytest.raises(ValueError):
            split_bubbles("x", 0, 1)
