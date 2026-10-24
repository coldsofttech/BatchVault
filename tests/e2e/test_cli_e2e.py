"""End-to-end tests of the installed ``batchvault`` command.

Each test runs the real program in a separate process, the way a user would:
real arguments, real files, real exit codes, real standard output.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from batchvault.engines.sevenzip import SevenZipEngine

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")
ENGINES = [
    "native",
    pytest.param(
        "7zip",
        marks=pytest.mark.skipif(
            not SevenZipEngine().is_available(), reason="7-Zip is not installed"
        ),
    ),
]


def _command() -> list[str]:
    script = shutil.which("batchvault")
    return [script] if script else [sys.executable, "-m", "batchvault"]


@pytest.fixture
def cli(tmp_path):
    """Run batchvault in ``tmp_path``; returns the finished process."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BATCHVAULT_")}
    env.update(
        PYTHONUTF8="1",
        NO_COLOR="1",
        COLUMNS="200",
        BATCHVAULT_LOG_DIR=str(tmp_path / "logs"),
    )

    def run(*args, cwd=None, input=None, extra_env=None):
        return subprocess.run(
            [*_command(), *map(str, args)],
            cwd=cwd or tmp_path,
            env={**env, **(extra_env or {})},
            input=input,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    return run


def code(process) -> int:
    return process.returncode


def assert_restored(data_dir: Path, restored: Path, names: set[str] | None = None):
    """Every original is back at the same path below ``restored``."""
    checked = 0
    for original in data_dir.rglob("*"):
        if original.is_file() and (names is None or original.name in names):
            copy = restored / original.relative_to(data_dir)
            assert copy.read_bytes() == original.read_bytes()
            checked += 1
    assert checked


def manifest_entries(archives: Path) -> list[dict]:
    text = (archives / "manifest.json").read_text(encoding="utf-8")
    return json.loads(text)["entries"]


def test_version_help_and_module_entry_point(cli):
    version = cli("--version")
    assert code(version) == 0 and "batchvault" in version.stdout

    for command in ("pack", "unpack", "verify", "inspect", "list", "doctor"):
        assert code(cli(command, "--help")) == 0

    module = subprocess.run(
        [sys.executable, "-m", "batchvault", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert code(module) == 0 and module.stdout == version.stdout


@pytest.mark.parametrize("engine", ENGINES)
def test_full_journey(cli, tmp_path, data_dir, engine):
    listing = tmp_path / "files.json"
    assert code(cli("list", "files", data_dir, "-o", listing)) == 0
    assert len(json.loads(listing.read_text(encoding="utf-8"))) == 5

    packed = cli(
        "pack", "--list", listing, "-o", "archives", "--engine", engine,
        "--volume-size", "256k", "--verify", "--json",
    )  # fmt: skip
    assert code(packed) == 0, packed.stderr
    result = json.loads(packed.stdout)
    assert result["status"] == "success"
    assert result["engine"] == engine
    volumes = {Path(e["source"]).name: e["volumes"] for e in result["entries"]}
    assert len(volumes["report.bin"]) > 1

    inspected = json.loads(cli("inspect", "archives", "--json").stdout)
    assert len(inspected) == 5 and all(e["complete"] for e in inspected)

    checked = cli("verify", "archives", "--against", data_dir, "--json")
    assert code(checked) == 0, checked.stdout
    assert json.loads(checked.stdout)["status"] == "success"

    # The folder that was listed ("data") and the sub folder inside it are kept.
    assert (tmp_path / "archives/data/report.bin").is_dir()
    assert (tmp_path / "archives/data/sub dir/nested file.csv").is_dir()

    restored = tmp_path / "restored"
    assert code(cli("unpack", "archives", "-o", restored, "-q")) == 0
    assert_restored(data_dir, restored / "data")


@pytest.mark.parametrize("engine", ENGINES)
def test_update_only_repacks_what_changed(cli, tmp_path, data_dir, engine):
    notes = data_dir / "notes.txt"
    base = [
        "pack", notes, data_dir / "report.bin", "-o", "a",
        "--engine", engine, "--json",
    ]  # fmt: skip
    assert code(cli(*base)) == 0
    assert code(cli(*base)) == 1  # already there: skipped

    unchanged = json.loads(cli(*base, "--update").stdout)
    assert {e["message"] for e in unchanged["entries"]} == {"Up to date."}

    notes.write_text("changed\n" * 1000, encoding="utf-8")
    later = notes.stat().st_mtime + 30
    os.utime(notes, (later, later))

    changed = json.loads(cli(*base, "--update").stdout)
    statuses = {Path(e["source"]).name: e["status"] for e in changed["entries"]}
    assert statuses == {"notes.txt": "success", "report.bin": "skipped"}

    assert code(cli("unpack", "a", "-o", "restored", "-q")) == 0
    assert (tmp_path / "restored" / "notes.txt").read_bytes() == notes.read_bytes()


def test_pipeline_through_standard_input(cli, data_dir):
    listed = cli("list", "files", data_dir, "--exclude", "*.dat")
    assert code(listed) == 0

    packed = cli(
        "pack", "--list", "-", "-o", "a", "--engine", "native", "--json",
        input=listed.stdout,
    )  # fmt: skip
    assert code(packed) == 0, packed.stderr
    names = {Path(e["source"]).name for e in json.loads(packed.stdout)["entries"]}
    assert names == {"report.bin", "notes.txt", "ünï cödé.txt", "nested file.csv"}


def test_json_events_are_one_object_per_line(cli, data_dir):
    run = cli(
        "pack", data_dir / "notes.txt", "-o", "a", "--engine", "native",
        "--json-events",
    )  # fmt: skip
    assert code(run) == 0
    events = [json.loads(line) for line in run.stdout.splitlines()]
    assert [e["event"] for e in events] == ["plan", "start", "finish", "result"]


def test_exit_codes(cli, tmp_path, data_dir):
    notes = data_dir / "notes.txt"
    pack = ["pack", notes, "-o", "a", "--engine", "native", "-q"]
    assert code(cli(*pack)) == 0
    assert code(cli(*pack)) == 1
    assert code(cli("pack", tmp_path / "nope.txt", "-o", "b", "-q")) == 2
    assert code(cli("pack", "-o", "b", "-q")) == 2
    assert code(cli("unpack", tmp_path / "nowhere", "-q")) == 2
    assert code(cli("not-a-command")) == 2


@pytest.mark.parametrize("engine", ENGINES)
def test_damaged_archives_are_never_restored(cli, tmp_path, data_dir, engine):
    packed = cli(
        "pack", data_dir / "report.bin", "-o", "a", "--engine", engine,
        "--volume-size", "256k", "-q",
    )  # fmt: skip
    assert code(packed) == 0

    volume = sorted((tmp_path / "a" / "report.bin").glob("*.00*"))[2]
    damaged = bytearray(volume.read_bytes())
    damaged[len(damaged) // 2] ^= 0xFF
    volume.write_bytes(bytes(damaged))

    assert code(cli("verify", "a", "-q")) == 1
    assert code(cli("unpack", "a", "-o", "restored", "-q")) == 1
    assert not (tmp_path / "restored" / "report.bin").exists()


def test_restore_from_one_volume_without_a_manifest(cli, tmp_path, data_dir):
    packed = cli(
        "pack", data_dir / "report.bin", "-o", "a", "--engine", "native",
        "--volume-size", "256k", "-q",
    )  # fmt: skip
    assert code(packed) == 0
    (tmp_path / "a" / "manifest.json").unlink()

    volume = sorted((tmp_path / "a" / "report.bin").glob("*.bv.*"))[3]
    restored = cli("unpack", volume, "-o", "restored", "--json")
    assert code(restored) == 0
    assert json.loads(restored.stdout)["warnings"]  # checksums not verified
    assert_restored(data_dir, tmp_path / "restored", {"report.bin"})


def test_settings_come_from_a_config_file(cli, tmp_path, data_dir):
    (tmp_path / "batchvault.toml").write_text(
        '[pack]\nengine = "native"\nlevel = 7\nvolume-size = "64k"\n', encoding="utf-8"
    )
    assert code(cli("pack", data_dir / "report.bin", "-o", "a", "-q")) == 0

    entry = manifest_entries(tmp_path / "a")[0]
    assert entry["level"] == 7 and len(entry["volumes"]) > 1

    override = cli("pack", data_dir / "notes.txt", "-o", "b", "--level", "2", "-q")
    assert code(override) == 0
    assert manifest_entries(tmp_path / "b")[0]["level"] == 2  # the command line wins


def test_many_files_in_parallel(cli, tmp_path):
    folder = tmp_path / "many"
    folder.mkdir()
    for number in range(12):
        (folder / f"file {number:02}.bin").write_bytes(os.urandom(40_000))

    files = tmp_path / "many.txt"
    assert code(cli("list", "files", folder, "-o", files)) == 0

    packed = cli(
        "pack", "--list", files, "-o", "a", "--engine", "native", "-j", "4",
        "--volume-size", "16k", "--verify", "-q",
    )  # fmt: skip
    assert code(packed) == 0
    assert code(cli("verify", "a", "--against", folder, "-q")) == 0
    assert code(cli("unpack", "a", "-o", "restored", "-q", "-j", "4")) == 0
    assert_restored(folder, tmp_path / "restored" / "many")


def test_a_log_file_is_written(cli, tmp_path, data_dir):
    run = cli("pack", data_dir / "notes.txt", "-o", "a", "--engine", "native", "-q")
    assert code(run) == 0

    logs = list((tmp_path / "logs").glob("batchvault-*.log"))
    assert len(logs) == 1
    assert "notes.txt" in logs[0].read_text(encoding="utf-8")


def test_delete_source_only_after_proof(cli, tmp_path, data_dir):
    notes = data_dir / "notes.txt"
    original = notes.read_bytes()

    base = ["pack", notes, "-o", "a", "--engine", "native", "--delete-source", "-q"]
    assert code(cli(*base)) == 0
    assert notes.exists()  # no terminal and no --yes: nothing deleted

    assert code(cli(*base, "--yes", "--force")) == 0
    assert not notes.exists()

    assert code(cli("unpack", "a", "-o", "restored", "-q")) == 0
    assert (tmp_path / "restored" / "notes.txt").read_bytes() == original


def test_doctor(cli):
    report = cli("doctor", "--json")
    assert code(report) == 0
    assert {c["name"] for c in json.loads(report.stdout)} >= {"Python", "Log folder"}


@needs_crypto
def test_encrypted_and_signed_archives(cli, tmp_path, data_dir):
    notes = data_dir / "notes.txt"
    password = {"BATCHVAULT_PASSWORD": "correct horse"}
    wrong = {"BATCHVAULT_PASSWORD": "wrong"}

    keygen = cli("keygen", "--private", "k.key", "--public", "k.pub", "-q")
    assert code(keygen) == 0

    packed = cli(
        "pack", notes, "-o", "a", "--engine", "native", "--encrypt",
        "--sign-key", "k.key", "-q", extra_env=password,
    )  # fmt: skip
    assert code(packed) == 0

    # Missing, wrong and right password.
    assert code(cli("verify", "a", "-q")) == 1
    assert code(cli("verify", "a", "-q", extra_env=wrong)) == 1
    signed = cli("verify", "a", "--public-key", "k.pub", "-q", extra_env=password)
    assert code(signed) == 0

    # The contents are not readable in the volumes.
    volumes = list((tmp_path / "a" / "notes.txt").glob("*.bv.*"))
    assert b"hello world" not in b"".join(v.read_bytes() for v in volumes)

    restored = cli(
        "unpack", "a", "-o", "restored", "--public-key", "k.pub", "-q",
        extra_env=password,
    )  # fmt: skip
    assert code(restored) == 0
    assert (tmp_path / "restored" / "notes.txt").read_bytes() == notes.read_bytes()

    # A changed manifest is refused before anything is restored.
    manifest = tmp_path / "a" / "manifest.json"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace("notes.txt", "other.txt", 1), encoding="utf-8")
    refused = cli(
        "unpack", "a", "-o", "again", "--public-key", "k.pub", "-q",
        extra_env=password,
    )  # fmt: skip
    assert code(refused) == 2
    assert not (tmp_path / "again").exists()
