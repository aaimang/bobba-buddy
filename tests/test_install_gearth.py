"""Tests for scripts/install_gearth.py: finding G-Earth and the venv on each OS."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install_gearth.py"
_spec = importlib.util.spec_from_file_location("install_gearth", SCRIPT)
install_gearth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install_gearth)


def make_gearth(folder: Path, program: str = "G-Earth.jar", extensions_up: bool = False) -> Path:
    """Create a fake unzipped G-Earth and return its program file."""
    folder.mkdir(parents=True, exist_ok=True)
    program_path = folder / program
    program_path.write_text("")
    (folder.parent if extensions_up else folder).joinpath("Extensions").mkdir(exist_ok=True)
    return program_path


class TestGearthDir:
    def test_macos_uses_application_support(self):
        assert install_gearth.default_gearth_dir("darwin") == install_gearth.MAC_GEARTH_DIR
        assert install_gearth.MAC_GEARTH_DIR.parts[-3:] == (
            "Library",
            "Application Support",
            "G-Earth",
        )

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_windows_and_linux_have_no_fixed_folder(self, platform):
        assert install_gearth.default_gearth_dir(platform) is None

    def test_folder_is_used_as_is(self, tmp_path):
        assert install_gearth.resolve_gearth_dir(tmp_path) == tmp_path

    @pytest.mark.parametrize("name", ["G-Earth.jar", "G-Earth-1.5.4.JAR", "G-Earth.exe"])
    def test_program_path_means_its_folder(self, tmp_path, name):
        assert install_gearth.resolve_gearth_dir(tmp_path / name) == tmp_path


class TestVenvPython:
    def test_windows_layout(self, tmp_path):
        assert install_gearth.venv_python(tmp_path, "win32") == (
            tmp_path / ".venv" / "Scripts" / "python.exe"
        )

    @pytest.mark.parametrize("platform", ["darwin", "linux"])
    def test_posix_layout(self, tmp_path, platform):
        expected = tmp_path / ".venv" / "bin" / "python"
        assert install_gearth.venv_python(tmp_path, platform) == expected


class TestProgramNames:
    @pytest.mark.parametrize(
        "path",
        [
            "G-Earth.jar",
            "/opt/G-Earth/G-Earth",
            r"C:\Tools\G-Earth\G-Earth.exe",
            "g-earth-1.5.4.jar",
        ],
    )
    def test_recognised(self, path):
        assert install_gearth.is_gearth_program(path)

    @pytest.mark.parametrize("path", ["java", "G-Earth-Api.txt", "/usr/bin/javaw", "Earth.jar"])
    def test_not_recognised(self, path):
        assert not install_gearth.is_gearth_program(path)


class TestWindowsProcesses:
    def test_launcher_exe_from_executable_path(self):
        exe = r"C:\Users\me\Downloads\G-Earth\G-Earth.exe"
        line = f'"{exe}" "{exe}"'  # executable path, then the command line
        assert install_gearth.programs_in_command_line(line) == [exe, exe]

    def test_jar_in_java_command_line(self):
        line = r'"C:\Java\bin\javaw.exe" javaw -jar "C:\My Tools\G-Earth\G-Earth.jar"'
        assert install_gearth.programs_in_command_line(line) == [r"C:\My Tools\G-Earth\G-Earth.jar"]

    def test_unrelated_java_is_ignored(self):
        assert install_gearth.programs_in_command_line(r'"C:\Java\java.exe" -jar other.jar') == []

    def test_running_programs_are_deduplicated(self, monkeypatch):
        line = r'"C:\G\G-Earth.exe" "C:\G\G-Earth.exe"'
        monkeypatch.setattr(install_gearth, "_windows_process_command_lines", lambda: [line])
        assert install_gearth.running_gearth_programs("win32") == [Path(r"C:\G\G-Earth.exe")]


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="Linux paths and symlinks don't apply on Windows"
)


class TestLinuxProcesses:
    def test_relative_jar_uses_process_folder(self, tmp_path):
        found = install_gearth.programs_in_arguments(["java", "-jar", "G-Earth.jar"], tmp_path)
        assert found == [tmp_path / "G-Earth.jar"]

    @posix_only
    def test_classpath_entries(self, tmp_path):
        args = ["java", "-cp", "lib/a.jar:/opt/G-Earth/G-Earth.jar", "gearth.Main"]
        assert install_gearth.programs_in_arguments(args, tmp_path) == [
            Path("/opt/G-Earth/G-Earth.jar")
        ]

    @posix_only
    def test_reads_proc(self, tmp_path):
        proc = tmp_path / "proc"
        gearth = tmp_path / "G-Earth"
        gearth.mkdir()
        pid = proc / "123"
        pid.mkdir(parents=True)
        (pid / "cmdline").write_bytes(b"java\0-jar\0G-Earth.jar\0")
        (pid / "cwd").symlink_to(gearth)
        (proc / "self").mkdir()  # not a pid: skipped
        (proc / "456").mkdir()  # no cmdline: skipped
        processes = install_gearth._linux_process_arguments(proc)
        assert processes == [(gearth.resolve(), ["java", "-jar", "G-Earth.jar"])]

    def test_macos_has_no_process_lookup(self):
        assert install_gearth.running_gearth_programs("darwin") == []


class TestFolderSearch:
    def test_extensions_next_to_program(self, tmp_path):
        program = make_gearth(tmp_path / "G-Earth")
        assert install_gearth.gearth_dir_for(program) == tmp_path / "G-Earth"

    def test_extensions_one_level_up(self, tmp_path):
        program = make_gearth(tmp_path / "G-Earth" / "app", extensions_up=True)
        assert install_gearth.gearth_dir_for(program) == tmp_path / "G-Earth"

    def test_no_extensions_folder(self, tmp_path):
        (tmp_path / "G-Earth.jar").write_text("")
        assert install_gearth.gearth_dir_for(tmp_path / "G-Earth.jar") is None

    def test_finds_unzipped_gearth_in_downloads(self, tmp_path):
        program = make_gearth(
            tmp_path / "Downloads" / "G-Earth-windows-x64" / "G-Earth", "G-Earth.exe"
        )
        found = install_gearth.search_gearth_programs(install_gearth.search_roots(tmp_path))
        assert found == [program.resolve()]

    def test_skips_hidden_and_tool_folders_and_respects_depth(self, tmp_path):
        make_gearth(tmp_path / ".cache" / "G-Earth")
        make_gearth(tmp_path / "proj" / ".venv" / "G-Earth")
        make_gearth(tmp_path / "a" / "b" / "c" / "d" / "G-Earth")
        assert install_gearth.search_gearth_programs([(tmp_path, 3)]) == []

    def test_home_is_searched_shallow(self, tmp_path):
        near = make_gearth(tmp_path / "G-Earth")
        make_gearth(tmp_path / "Projects" / "old" / "G-Earth")  # too deep under home
        found = install_gearth.search_gearth_programs(install_gearth.search_roots(tmp_path))
        assert found == [near.resolve()]

    def test_roots_are_not_walked_twice(self, tmp_path):
        make_gearth(tmp_path / "Downloads" / "G-Earth")
        seen = []
        real_scandir = install_gearth.os.scandir

        def counting_scandir(path):
            seen.append(Path(path).resolve())
            return real_scandir(path)

        install_gearth.os.scandir, saved = counting_scandir, real_scandir
        try:
            install_gearth.search_gearth_programs(install_gearth.search_roots(tmp_path))
        finally:
            install_gearth.os.scandir = saved
        assert seen.count((tmp_path / "Downloads").resolve()) == 1

    def test_time_limit_stops_the_search(self, tmp_path):
        make_gearth(tmp_path / "Downloads" / "G-Earth")
        roots = install_gearth.search_roots(tmp_path)
        assert install_gearth.search_gearth_programs(roots, time_limit=-1) == []

    @pytest.mark.parametrize("name", ["Photos Library.photoslibrary", "G-Earth.app", "Pictures"])
    def test_skips_libraries_and_bundles(self, tmp_path, name):
        make_gearth(tmp_path / name)
        assert install_gearth.search_gearth_programs([(tmp_path, 3)]) == []

    def test_same_folder_found_twice_counts_once(self, tmp_path):
        program = make_gearth(tmp_path / "G-Earth")
        assert install_gearth.find_gearth_dirs([program, program]) == [
            (tmp_path / "G-Earth").resolve()
        ]


class TestLocate:
    def test_gearth_dir_wins(self, tmp_path):
        assert install_gearth.locate_gearth_dir(tmp_path, "win32")[0] == tmp_path

    def test_macos_default(self, tmp_path):
        found, how = install_gearth.locate_gearth_dir(None, "darwin", tmp_path)
        assert found == install_gearth.MAC_GEARTH_DIR and "macOS" in how

    def test_running_gearth_first(self, monkeypatch, tmp_path):
        running = make_gearth(tmp_path / "Running")
        make_gearth(tmp_path / "Downloads" / "G-Earth")
        monkeypatch.setattr(install_gearth, "running_gearth_programs", lambda p: [running])
        found, how = install_gearth.locate_gearth_dir(None, "win32", tmp_path)
        assert found == (tmp_path / "Running").resolve() and "running" in how

    def test_search_when_not_running(self, monkeypatch, tmp_path):
        make_gearth(tmp_path / "Desktop" / "G-Earth", "G-Earth")
        monkeypatch.setattr(install_gearth, "running_gearth_programs", lambda p: [])
        found, how = install_gearth.locate_gearth_dir(None, "linux", tmp_path)
        assert found == (tmp_path / "Desktop" / "G-Earth").resolve() and "searching" in how

    def test_several_found_asks(self, monkeypatch, tmp_path):
        make_gearth(tmp_path / "Desktop" / "G-Earth")
        make_gearth(tmp_path / "Downloads" / "G-Earth")
        monkeypatch.setattr(install_gearth, "running_gearth_programs", lambda p: [])
        found, how = install_gearth.locate_gearth_dir(None, "win32", tmp_path)
        assert found is None and "several" in how

    def test_nothing_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(install_gearth, "running_gearth_programs", lambda p: [])
        found, how = install_gearth.locate_gearth_dir(None, "linux", tmp_path)
        assert found is None and "couldn't find" in how


class TestMain:
    def run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["install_gearth.py", *args])
        return install_gearth.main()

    def test_not_found_explains_gearth_dir(self, monkeypatch, capsys):
        monkeypatch.setattr(install_gearth, "locate_gearth_dir", lambda given: (None, "nope"))
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
        assert "would write" in out and "Bobba Buddy_" in out and "using --gearth-dir" in out
        assert not any((tmp_path / "Extensions").iterdir())

    def test_detected_folder_is_used(self, monkeypatch, capsys, tmp_path):
        (tmp_path / "Extensions").mkdir()
        monkeypatch.setattr(
            install_gearth,
            "locate_gearth_dir",
            lambda given: (tmp_path, "found the running G-Earth"),
        )
        assert self.run(monkeypatch, "--dry-run") == 0
        assert "found the running G-Earth" in capsys.readouterr().out


class TestDsStore:
    """Finder's .DS_Store in Extensions/ stops G-Earth's window from opening."""

    def test_removed(self, tmp_path, capsys):
        (tmp_path / ".DS_Store").write_bytes(b"\0\0\0\1Bud1")
        assert install_gearth.remove_ds_store(tmp_path)
        assert not (tmp_path / ".DS_Store").exists()
        assert "never opens its window" in capsys.readouterr().out

    def test_dry_run_only_reports(self, tmp_path, capsys):
        (tmp_path / ".DS_Store").write_bytes(b"x")
        assert install_gearth.remove_ds_store(tmp_path, dry_run=True)
        assert (tmp_path / ".DS_Store").exists()
        assert "would remove" in capsys.readouterr().out

    def test_missing_is_fine(self, tmp_path, capsys):
        assert not install_gearth.remove_ds_store(tmp_path)
        assert not install_gearth.remove_ds_store(tmp_path / "no-such-folder")
        assert capsys.readouterr().out == ""

    def test_other_files_untouched(self, tmp_path):
        (tmp_path / ".DS_Store").write_bytes(b"x")
        (tmp_path / "notes.txt").write_text("keep")
        (tmp_path / "Other_1.0").mkdir()
        (tmp_path / "Other_1.0" / ".DS_Store").write_bytes(b"x")
        install_gearth.remove_ds_store(tmp_path)
        assert (tmp_path / "notes.txt").read_text() == "keep"
        assert (tmp_path / "Other_1.0" / ".DS_Store").exists()  # not ours, not touched

    def run(self, monkeypatch, *args):
        monkeypatch.setattr(sys, "argv", ["install_gearth.py", *args])
        return install_gearth.main()

    def make_extensions(self, tmp_path):
        extensions = tmp_path / "Extensions"
        (extensions / "Other_1.0").mkdir(parents=True)
        (extensions / "Other_1.0" / ".DS_Store").write_bytes(b"x")
        (extensions / ".DS_Store").write_bytes(b"x")
        return extensions

    def test_install_cleans_extensions_and_own_folder(self, monkeypatch, tmp_path):
        extensions = self.make_extensions(tmp_path)
        ours = extensions / f"Bobba Buddy_{install_gearth.__version__}"
        ours.mkdir()
        (ours / ".DS_Store").write_bytes(b"x")
        python = tmp_path / "python"
        python.write_text("")
        monkeypatch.setattr(install_gearth, "venv_python", lambda project: python)
        assert self.run(monkeypatch, "--gearth-dir", str(tmp_path)) == 0
        assert not (extensions / ".DS_Store").is_file()  # a guard folder on macOS
        assert not (ours / ".DS_Store").exists()
        assert (ours / "command.txt").exists()
        assert (extensions / "Other_1.0" / ".DS_Store").exists()

    def test_uninstall_cleans_extensions(self, monkeypatch, tmp_path):
        extensions = self.make_extensions(tmp_path)
        assert self.run(monkeypatch, "--gearth-dir", str(tmp_path), "--uninstall") == 0
        assert not (extensions / ".DS_Store").is_file()
        assert (extensions / "Other_1.0").is_dir()

    def test_dry_run_reports_without_removing(self, monkeypatch, capsys, tmp_path):
        extensions = self.make_extensions(tmp_path)
        assert self.run(monkeypatch, "--gearth-dir", str(tmp_path), "--dry-run") == 0
        assert (extensions / ".DS_Store").exists()
        assert "would remove" in capsys.readouterr().out

    def test_guard_folder_on_macos(self, tmp_path, capsys):
        (tmp_path / ".DS_Store").write_bytes(b"x")
        install_gearth.guard_ds_store(tmp_path, platform="darwin")
        assert (tmp_path / ".DS_Store").is_dir()
        assert list((tmp_path / ".DS_Store").iterdir()) == []  # no command.txt: G-Earth skips it
        out = capsys.readouterr().out
        assert "removing" in out and "creating empty folder" in out

    def test_guard_is_kept_and_quiet_next_time(self, tmp_path, capsys):
        install_gearth.guard_ds_store(tmp_path, platform="darwin")
        capsys.readouterr()
        install_gearth.guard_ds_store(tmp_path, platform="darwin")
        assert (tmp_path / ".DS_Store").is_dir()
        assert capsys.readouterr().out == ""

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_no_guard_elsewhere(self, tmp_path, platform):
        (tmp_path / ".DS_Store").write_bytes(b"x")
        install_gearth.guard_ds_store(tmp_path, platform=platform)
        assert not (tmp_path / ".DS_Store").exists()

    def test_guard_dry_run(self, tmp_path, capsys):
        (tmp_path / ".DS_Store").write_bytes(b"x")
        install_gearth.guard_ds_store(tmp_path, dry_run=True, platform="darwin")
        assert (tmp_path / ".DS_Store").is_file()
        out = capsys.readouterr().out
        assert "would remove" in out and "would create" in out
