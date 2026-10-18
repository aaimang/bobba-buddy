"""Checking GitHub for a newer release (no network: GitHub is faked)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from bobba_buddy import updates
from bobba_buddy.updates import Release, UpdateChecker, UpdateError, fetch_latest, parse_version

DAY = 24 * 3600


def release(version: str) -> Release:
    return Release(
        version, f"v{version}", f"https://example.test/v{version}", "2026-10-17T00:00:00Z"
    )


class FakeGitHub:
    """Returns a release (or raises), counting calls."""

    def __init__(self, answer):
        """Answer with this release, or raise it if it's an exception."""
        self.answer = answer
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class Clock:
    def __init__(self, now: float = 1_000_000.0):
        """Start at a fixed time."""
        self.now = now

    def __call__(self) -> float:
        return self.now


class TestVersions:
    @pytest.mark.parametrize(
        ("text", "numbers"),
        [("1.4.1", (1, 4, 1)), ("v1.5.0", (1, 5, 0)), ("V2.0", (2, 0)), ("1.6.0-rc1", (1, 6, 0))],
    )
    def test_parse(self, text, numbers):
        assert parse_version(text) == numbers

    def test_compares_as_numbers(self):
        assert parse_version("1.10.0") > parse_version("1.9.9")

    def test_rejects_junk(self):
        with pytest.raises(ValueError):
            parse_version("latest")


class TestFetch:
    def fake_urlopen(self, monkeypatch, payload=None, error=None):
        def urlopen(request, timeout):
            assert "api.github.com/repos/aaimang/bobba-buddy/releases/latest" in request.full_url
            assert request.get_header("User-agent").startswith("bobba-buddy/")
            if error is not None:
                raise error
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)

    def test_reads_the_latest_release(self, monkeypatch):
        self.fake_urlopen(
            monkeypatch,
            {
                "tag_name": "v1.5.0",
                "html_url": "https://github.com/x/releases/tag/v1.5.0",
                "published_at": "2026-10-20T10:00:00Z",
            },
        )
        got = fetch_latest()
        assert got == Release(
            "1.5.0", "v1.5.0", "https://github.com/x/releases/tag/v1.5.0", "2026-10-20T10:00:00Z"
        )

    @pytest.mark.parametrize(
        "error",
        [
            urllib.error.HTTPError("u", 403, "rate limited", {}, None),
            urllib.error.URLError("offline"),
            TimeoutError("slow"),
        ],
    )
    def test_network_problems_become_update_errors(self, monkeypatch, error):
        self.fake_urlopen(monkeypatch, error=error)
        with pytest.raises(UpdateError):
            fetch_latest()

    @pytest.mark.parametrize("payload", [{"name": "no tag"}, {"tag_name": "latest"}, []])
    def test_unusable_answers(self, monkeypatch, payload):
        self.fake_urlopen(monkeypatch, payload)
        with pytest.raises(UpdateError):
            fetch_latest()


class TestChecker:
    def test_newer_release_is_available(self, tmp_path):
        github = FakeGitHub(release("1.5.0"))
        checker = UpdateChecker(tmp_path / "u.json", current="1.4.1", fetch=github)
        assert checker.check_now()
        assert checker.available and checker.latest.version == "1.5.0"

    @pytest.mark.parametrize("latest", ["1.4.1", "1.4.0"])
    def test_same_or_older_is_not(self, tmp_path, latest):
        checker = UpdateChecker(None, current="1.4.1", fetch=FakeGitHub(release(latest)))
        checker.check_now()
        assert not checker.available

    def test_checks_at_most_once_a_day(self):
        github, clock = FakeGitHub(release("1.5.0")), Clock()
        checker = UpdateChecker(None, current="1.4.1", fetch=github, clock=clock)
        assert checker.maybe_check()
        clock.now += DAY - 60
        assert not checker.maybe_check()
        clock.now += 120
        assert checker.maybe_check()
        assert github.calls == 2

    def test_off_means_no_automatic_check(self):
        github = FakeGitHub(release("1.5.0"))
        checker = UpdateChecker(None, current="1.4.1", fetch=github, enabled=lambda: False)
        assert not checker.maybe_check() and github.calls == 0
        checker.check_now()  # "Check now" still works
        assert github.calls == 1

    def test_failure_keeps_the_last_answer_and_never_raises(self):
        github = FakeGitHub(release("1.5.0"))
        checker = UpdateChecker(None, current="1.4.1", fetch=github)
        checker.check_now()
        github.answer = UpdateError("GitHub answered 403")
        assert not checker.check_now()
        assert checker.error == "GitHub answered 403"
        assert checker.available  # still knows about 1.5.0
        github.answer = RuntimeError("anything else")
        assert not checker.check_now()

    def test_dismiss_hides_only_that_version(self):
        github = FakeGitHub(release("1.5.0"))
        checker = UpdateChecker(None, current="1.4.1", fetch=github)
        checker.check_now()
        checker.dismiss()
        assert checker.dismissed
        github.answer = release("1.5.1")
        checker.check_now()
        assert not checker.dismissed

    def test_announce_once_per_version(self):
        github = FakeGitHub(release("1.5.0"))
        checker = UpdateChecker(None, current="1.4.1", fetch=github)
        notices = []
        assert not checker.announce(lambda *a: notices.append(a))  # nothing known yet
        checker.check_now()
        assert checker.announce(lambda *a: notices.append(a))
        assert not checker.announce(lambda *a: notices.append(a))
        assert notices == [
            ("Bobba Buddy 1.5.0 is available (you have 1.4.1). Press ▶ for details.", "blue")
        ]

    def test_state_survives_a_restart(self, tmp_path):
        path, clock = tmp_path / "state" / "u.json", Clock()
        first = UpdateChecker(
            path, current="1.4.1", fetch=FakeGitHub(release("1.5.0")), clock=clock
        )
        first.check_now()
        first.announce(lambda *a: None)
        first.dismiss()
        github = FakeGitHub(release("9.9.9"))
        second = UpdateChecker(path, current="1.4.1", fetch=github, clock=clock)
        assert second.latest.version == "1.5.0" and second.dismissed
        assert not second.announce(lambda *a: None)  # already announced
        assert not second.maybe_check() and github.calls == 0  # checked recently

    def test_broken_state_file_starts_fresh(self, tmp_path):
        path = tmp_path / "u.json"
        path.write_text("{not json")
        checker = UpdateChecker(path, current="1.4.1", fetch=FakeGitHub(release("1.5.0")))
        assert checker.latest is None and checker.checked_at == 0

    def test_install_once(self):
        updates.uninstall()
        try:
            first = updates.install(None)
            assert updates.install(None) is first and updates.current() is first
        finally:
            updates.uninstall()
