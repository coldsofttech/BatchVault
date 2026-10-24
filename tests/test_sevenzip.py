import os
import sys
from pathlib import Path

import pytest

from batchvault.engines.sevenzip import (
    ENV_VAR,
    SevenZipEngine,
    build_pack_command,
    find_7zip,
)
from batchvault.errors import EngineNotFoundError
from batchvault.options import PackOptions


def test_pack_command():
    options = PackOptions(level=3, volume_size=1024, jobs=4)
    command = build_pack_command(Path("7z"), Path("out/a.7z"), Path("a"), options)
    assert command[:2] == ["7z", "a"]
    assert "-mx=3" in command
    assert "-mmt=4" in command
    assert "-v1024b" in command
    assert command[-2:] == [str(Path("out/a.7z")), "a"]


def test_find_7zip_explicit_path(tmp_path):
    exe = tmp_path / "7z"
    exe.write_text("")
    assert find_7zip(exe) == exe


def test_find_7zip_explicit_path_missing(tmp_path):
    with pytest.raises(EngineNotFoundError):
        find_7zip(tmp_path / "nope")


def test_find_7zip_uses_environment(tmp_path, monkeypatch):
    exe = tmp_path / "7zz"
    exe.write_text("")
    monkeypatch.setenv(ENV_VAR, str(exe))
    assert find_7zip() == exe


def test_engine_unavailable_when_path_wrong(tmp_path):
    assert not SevenZipEngine(tmp_path / "nope").is_available()


# --- the current folder is never searched for 7-Zip --------------------------


def _fake_executable(folder, name="7zz"):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + (".exe" if sys.platform == "win32" else ""))
    path.write_text("not really 7-Zip")
    path.chmod(0o755)
    return path


@pytest.fixture
def clean_environment(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)


def test_program_in_the_current_folder_is_not_used(tmp_path, monkeypatch,
                                                   clean_environment):
    planted = _fake_executable(tmp_path)
    monkeypatch.chdir(tmp_path)
    # Even with the current folder (and empty entries) on PATH.
    monkeypatch.setenv("PATH", os.pathsep.join([str(tmp_path), "", "."]))

    try:
        found = find_7zip()
    except EngineNotFoundError:
        return  # nothing else on this machine's well-known paths either
    assert found.resolve() != planted.resolve()


def test_relative_path_entries_are_ignored(tmp_path, monkeypatch, clean_environment):
    _fake_executable(tmp_path / "bin")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", "bin")

    try:
        found = find_7zip()
    except EngineNotFoundError:
        return
    assert found.resolve().parent != (tmp_path / "bin").resolve()


def test_program_in_a_real_path_folder_is_found(tmp_path, monkeypatch,
                                                clean_environment):
    elsewhere = tmp_path / "tools"
    wanted = _fake_executable(elsewhere)
    work = tmp_path / "work"
    work.mkdir()
    _fake_executable(work)  # a decoy in the folder we run from
    monkeypatch.chdir(work)
    monkeypatch.setenv("PATH", os.pathsep.join([str(work), str(elsewhere)]))

    assert find_7zip().resolve() == wanted.resolve()
