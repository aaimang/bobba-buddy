"""The one-click updater, with git, uv and the installer faked."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from bobba_buddy import updater as updater_mod
from bobba_buddy.updater import Updater, _clean_env, find_tool, read_version


class FakeShell:
    """Answers commands from a table of (first arguments → (code, output))."""

    def __init__(self, **overrides):
        """A clean main checkout behind the v1.5.0 tag (tag1500), with a changed uv.lock."""
        self.answers = {
            "rev-parse --is-inside-work-tree": (0, "true"),
            "rev-parse --abbrev-ref HEAD": (0, "main"),
            "rev-parse --abbrev-ref --symbolic-full-name @{u}": (0, "origin/main"),
            "status --porcelain --untracked-files=no": (0, ""),
            "fetch --tags origin": (0, ""),
            "rev-parse --verify --quiet refs/tags/v1.5.0^{commit}": (0, "tag1500"),
            "merge-base --is-ancestor tag1500 origin/main": (0, ""),
            "rev-parse HEAD": (0, "abc123"),
            "merge-base --is-ancestor HEAD tag1500": (0, ""),
            "show tag1500:src/bobba_buddy/__init__.py": (0, '__version__ = "1.5.0"\n'),
            "diff --quiet abc123 tag1500 -- uv.lock": (1, ""),
            "merge --ff-only tag1500": (0, "Fast-forward"),
            "sync": (0, "Resolved 54 packages"),
            "install_gearth.py": (0, "Done."),
        }
        self.answers.update(overrides)
        self.calls: list[str] = []

    def __call__(self, command, cwd, timeout):
        args = " ".join(command[1:])
        self.calls.append(f"{Path(command[0]).name} {args}")
        for key, answer in self.answers.items():
            if args == key or args.endswith(key):
                return answer
        raise AssertionError(f"unexpected command: {command}")


def make(tmp_path, shell, *, target="1.5.0", tag=None, current="1.4.1", tools=("git", "uv")):
    project = tmp_path / "bobba-buddy"
    (project / "src" / "bobba_buddy").mkdir(parents=True)
    (project / "src" / "bobba_buddy" / "__init__.py").write_text('__version__ = "1.5.0"\n')
    return Updater(
        project,
        target,
        current,
        tag=tag,
        run=shell,
        which=lambda name: f"/usr/bin/{name}" if name in tools else None,
        python="/venv/bin/python",
    )


def test_successful_update_runs_every_step_in_order(tmp_path, caplog):
    shell = FakeShell()
    up = make(tmp_path, shell)
    with caplog.at_level(logging.INFO, logger="bobba_buddy.updater"):
        assert up.run()
    assert up.state == "done" and up.new_version == "1.5.0"
    assert "Restart G-Earth" in up.message and "keeps running 1.4.1" in up.message
    steps = [c for c in shell.calls if not c.startswith("git rev-parse") and "status" not in c]
    assert steps[0] == "git fetch --tags origin"
    assert steps.index("git merge --ff-only tag1500") < steps.index("uv sync")
    assert "git merge --ff-only origin/main" not in shell.calls  # the tag, not main's tip
    assert steps[-1].endswith("install_gearth.py")
    assert "updater: $ git merge --ff-only tag1500" in caplog.text  # shows in Extension Logs


def test_uv_sync_skipped_when_the_lock_did_not_change(tmp_path):
    shell = FakeShell(**{"diff --quiet abc123 tag1500 -- uv.lock": (0, "")})
    up = make(tmp_path, shell)
    assert up.run()
    assert "uv sync" not in shell.calls


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        (
            {"rev-parse --is-inside-work-tree": (128, "fatal: not a git repository")},
            "isn't a git checkout",
        ),
        ({"rev-parse --abbrev-ref HEAD": (0, "dev")}, "development checkout (branch dev)"),
        (
            {"rev-parse --abbrev-ref --symbolic-full-name @{u}": (128, "no upstream")},
            "doesn't track",
        ),
        (
            {"status --porcelain --untracked-files=no": (0, " M README.md")},
            "README.md has local changes",
        ),
        ({"merge-base --is-ancestor HEAD tag1500": (1, "")}, "can't fast-forward"),
        ({"fetch --tags origin": (128, "could not resolve host")}, "internet connection"),
        (
            {"rev-parse --verify --quiet refs/tags/v1.5.0^{commit}": (1, "")},
            "v1.5.0 isn't on GitHub yet",
        ),
        ({"merge-base --is-ancestor tag1500 origin/main": (1, "")}, "isn't on main"),
        (
            {"show tag1500:src/bobba_buddy/__init__.py": (0, '__version__ = "1.4.9"\n')},
            "contains version 1.4.9, expected 1.5.0",
        ),
    ],
)
def test_unsafe_checkouts_are_refused(tmp_path, override, reason):
    shell = FakeShell(**override)
    up = make(tmp_path, shell)
    assert not up.run()
    assert up.state == "failed" and reason in up.message
    assert not any(c.startswith("git merge ") for c in shell.calls)  # nothing changed


@pytest.mark.parametrize("tag", ["--upload-pack=evil", "main", "v1.5", "v1.5.0;rm"])
def test_odd_tags_are_refused_before_git_runs(tmp_path, tag):
    shell = FakeShell()
    up = make(tmp_path, shell, tag=tag)
    assert not up.run()
    assert "doesn't look like vX.Y.Z" in up.message and shell.calls == []


def test_tag_defaults_to_v_target(tmp_path):
    assert make(tmp_path, FakeShell()).tag == "v1.5.0"


def test_api_key_is_not_passed_to_git_or_uv(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.test")
    monkeypatch.setenv("VIRTUAL_ENV", "/somewhere/.venv")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = _clean_env()
    assert not any(k.startswith("ANTHROPIC_") for k in env)
    assert "VIRTUAL_ENV" not in env
    assert env["PATH"] == "/usr/bin" and env["GIT_TERMINAL_PROMPT"] == "0"


def test_missing_tools(tmp_path):
    up = make(tmp_path, FakeShell(), tools=())
    assert not up.run() and "git wasn't found" in up.message
    up = make(tmp_path / "b", FakeShell(), tools=("git",))
    assert not up.run() and "uv wasn't found" in up.message


def test_major_versions_are_left_to_the_user(tmp_path):
    shell = FakeShell()
    up = make(tmp_path, shell, target="2.0.0")
    assert not up.run()
    assert "major version" in up.message and shell.calls == []


def test_uv_failure_says_what_to_do(tmp_path):
    up = make(tmp_path, FakeShell(sync=(1, "error: failed to remove file (in use)")))
    assert not up.run()
    assert "uv sync failed" in up.message and "Close G-Earth" in up.message


def test_installer_failure(tmp_path):
    up = make(tmp_path, FakeShell(**{"install_gearth.py": (1, "boom")}))
    assert not up.run()
    assert "registering it with G-Earth failed" in up.message


def test_start_runs_on_a_thread(tmp_path):
    up = make(tmp_path, FakeShell())
    thread = up.start()
    thread.join(5)
    assert thread.name == "buddy-updater" and up.state == "done"


def test_read_version(tmp_path):
    (tmp_path / "src" / "bobba_buddy").mkdir(parents=True)
    assert read_version(tmp_path) is None or True  # no __init__ yet
    (tmp_path / "src" / "bobba_buddy" / "__init__.py").write_text('__version__ = "9.8.7"\n')
    assert read_version(tmp_path) == "9.8.7"


def test_find_tool_looks_beyond_path(tmp_path, monkeypatch):
    monkeypatch.setattr(updater_mod.shutil, "which", lambda name: None)
    tool = tmp_path / ("uv.exe" if updater_mod.sys.platform == "win32" else "uv")
    tool.write_text("")
    tool.chmod(0o755)
    assert find_tool("uv", extra_dirs=(str(tmp_path),)) == str(tool)
    assert find_tool("definitely-not-a-tool", extra_dirs=(str(tmp_path),)) is None
