"""Tests for scripts/install_gearth.py: G-Earth folder and venv paths on each OS."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_gearth.py"
_spec = importlib.util.spec_from_file_location("install_gearth", SCRIPT)
install_gearth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_gearth)


class TestGearthDir:
    def test_macos_uses_application_support(self):
        assert install_gearth.default_gearth_dir("darwin") == install_gearth.MAC_GEARTH_DIR
        assert install_gearth.MAC_GEARTH_DIR.parts[-3:] == (
            "Library",
            "Application Support",
            "G-Earth",
        )

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_windows_and_linux_need_the_folder(self, platform):
        assert install_gearth.default_gearth_dir(platform) is None

    def test_folder_is_used_as_is(self, tmp_path):
        assert install_gearth.resolve_gearth_dir(tmp_path) == tmp_path

    @pytest.mark.parametrize("name", ["G-Earth.jar", "G-Earth-1.5.4.JAR"])
    def test_jar_path_means_its_folder(self, tmp_path, name):
        assert install_gearth.resolve_gearth_dir(tmp_path / name) == tmp_path


class TestVenvPython:
    def test_windows_layout(self, tmp_path):
        assert install_gearth.venv_python(tmp_path, "win32") == (
            tmp_path / ".venv" / "Scripts" / "python.exe"
        )

    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_posix_layout(self, tmp_path, platform):
        assert (
            install_gearth.venv_python(tmp_path, platform) == tmp_path / ".venv" / "bin" / "python"
        )


class TestMain:
    def run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["install_gearth.py", *args])
        return install_gearth.main()

    def test_no_folder_on_windows_explains_gearth_dir(self, monkeypatch, capsys):
        monkeypatch.setattr(install_gearth, "default_gearth_dir", lambda: None)
        assert self.run(monkeypatch) == 1
        assert "--gearth-dir" in capsys.readouterr().err

    def test_missing_extensions_folder(self, monkeypatch, capsys, tmp_path):
        assert self.run(monkeypatch, "--gearth-dir", str(tmp_path)) == 1
        assert "start G-Earth once" in capsys.readouterr().err

    def test_dry_run_with_jar_path(self, monkeypatch, capsys, tmp_path):
        (tmp_path / "Extensions").mkdir()
        assert (
            self.run(monkeypatch, "--gearth-dir", str(tmp_path / "G-Earth.jar"), "--dry-run") == 0
        )
        out = capsys.readouterr().out
        assert "would write" in out and "Bobba Buddy_" in out
        assert not any((tmp_path / "Extensions").iterdir())
