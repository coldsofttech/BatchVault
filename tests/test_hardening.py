"""Regression tests for the security fixes."""

import io
import json
import lzma
import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

import batchvault as bv
from batchvault.cli import main
from batchvault.engines.native import NativeEngine
from batchvault.engines.sevenzip import (
    SevenZipEngine,
    build_pack_command,
    build_test_command,
    build_unpack_command,
    folder_size,
)
from batchvault.errors import EngineError
from batchvault.options import MIB, PackOptions
from batchvault.security import redact_arguments

REPO = Path(__file__).resolve().parents[1]
engine = NativeEngine(block_size=4096)


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello " * 10_000, encoding="utf-8")
    return folder


def run(*args):
    return main([*args, "--no-log"])


# --- webhook secrets stay out of the log -------------------------------------


def test_redact_arguments_hides_webhook_values():
    argv = ["pack", "a.txt", "--notify", "https://h.example/T0/B0/SECRET", "-q"]
    assert redact_arguments(argv) == ["pack", "a.txt", "--notify", "***", "-q"]

    assert redact_arguments(["--notify=https://h.example/SECRET"]) == ["--notify=***"]
    assert redact_arguments(["--notify-on", "always"]) == ["--notify-on", "always"]
    assert redact_arguments(["https://u:pw@host/x"]) == ["https://***@host/x"]


def test_webhook_secret_is_not_written_to_the_log(tmp_path, media):
    log = tmp_path / "run.log"
    url = "http://127.0.0.1:1/services/SECRETTOKEN123"

    code = main([
        "pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "-q", "--notify", url, "--notify-on", "always",
        "--log-file", str(log),
    ])  # fmt: skip
    assert code == 0  # the unreachable webhook only warns

    text = log.read_text(encoding="utf-8")
    assert "SECRETTOKEN123" not in text
    assert "--notify ***" in text


def test_plain_http_webhook_gets_a_warning(tmp_path, media, capsys):
    code = run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
               "--engine", "native", "--dry-run", "-q",
               "--notify", "http://hooks.example.com/x")  # fmt: skip
    assert code == 0
    assert "unencrypted" in capsys.readouterr().err

    run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "--dry-run", "-q",
        "--notify", "http://127.0.0.1:9/x")  # fmt: skip
    assert "unencrypted" not in capsys.readouterr().err


# --- 7-Zip: wildcards, links, bombs ------------------------------------------


def test_wildcards_in_file_names_are_not_patterns():
    options = PackOptions()
    exe, src = Path("7z"), Path("a*b.txt")
    assert "-spd" in build_pack_command(exe, Path("out.7z"), src, options)
    assert "-spd" in build_unpack_command(exe, Path("x.7z.001"), Path("o"))
    assert "-spd" in build_test_command(exe, Path("x.7z.001"))


@pytest.fixture
def fake_7zip(tmp_path):
    exe = tmp_path / "7z"
    exe.write_text("")
    volume = tmp_path / "a.7z.001"
    volume.write_bytes(b"x")
    return SevenZipEngine(exe), volume


def test_links_in_7zip_output_are_refused(tmp_path, fake_7zip):
    sevenzip, volume = fake_7zip

    def fake_run(command):
        target = next(arg[2:] for arg in command if arg.startswith("-o"))
        try:
            (Path(target) / "link.txt").symlink_to(volume)
        except (OSError, NotImplementedError):
            pytest.skip("symbolic links are not available here")
        return ""

    sevenzip._run = fake_run
    with pytest.raises(EngineError, match="link or special"):
        sevenzip.unpack(volume, tmp_path / "out")
    assert not (tmp_path / "out" / "link.txt").exists()


def test_a_limit_makes_7zip_extract_under_watch(tmp_path, fake_7zip):
    sevenzip, volume = fake_7zip
    calls = []

    def fake_limited(command, watch, max_bytes):
        calls.append((command[1], watch.is_dir(), max_bytes))
        raise EngineError("would expand beyond the allowed size")

    sevenzip._run_limited = fake_limited
    with pytest.raises(EngineError, match="allowed size"):
        sevenzip.unpack(volume, tmp_path / "out", max_bytes=1000)
    assert calls == [("e", True, 1000)]
    assert list((tmp_path / "out").iterdir()) == []  # staging folder cleaned up


WRITER = """
import sys, time
from pathlib import Path
folder = Path(sys.argv[1])
for number in range(400):                  # up to about 400 MB
    (folder / f"chunk{number}.bin").write_bytes(bytes(1024 * 1024))
    time.sleep(0.02)
"""


def test_extraction_is_stopped_when_it_grows_past_the_limit(tmp_path, fake_7zip):
    sevenzip, _ = fake_7zip
    watched = tmp_path / "watched"
    watched.mkdir()

    with pytest.raises(EngineError, match="allowed size"):
        sevenzip._run_limited(
            [sys.executable, "-c", WRITER, str(watched)], watched, 3 * MIB
        )
    assert folder_size(watched) < 100 * MIB  # cut off long before it finished


def test_a_program_within_the_limit_runs_to_the_end(tmp_path, fake_7zip):
    sevenzip, _ = fake_7zip
    script = (
        "import sys, pathlib; "
        "pathlib.Path(sys.argv[1], 'small.bin').write_bytes(bytes(1000)); "
        "print('done')"
    )
    output = sevenzip._run_limited(
        [sys.executable, "-c", script, str(tmp_path)], tmp_path, MIB
    )
    assert "done" in output


def test_failures_still_carry_the_program_output(tmp_path, fake_7zip):
    sevenzip, _ = fake_7zip
    script = "import sys; print('Wrong password?'); sys.exit(2)"
    with pytest.raises(EngineError, match=r"(?s)exit code 2.*Wrong password"):
        sevenzip._run_limited([sys.executable, "-c", script], tmp_path, MIB)


# --- native: decompression bombs ---------------------------------------------


def make_volume(path: Path, member_size: int, trailing: int = 0) -> Path:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("bomb.txt")
        info.size = member_size
        tar.addfile(info, io.BytesIO(bytes(member_size)))
    path.write_bytes(lzma.compress(buffer.getvalue() + bytes(trailing)))
    return path


def test_oversized_member_is_refused_before_anything_is_written(tmp_path):
    volume = make_volume(tmp_path / "bomb.txt.bv.001", 5 * MIB)
    out = tmp_path / "out"

    with pytest.raises(EngineError, match="allowed size"):
        engine.unpack(volume, out, max_bytes=MIB)
    assert list(out.iterdir()) == []  # not even a staging folder is left


def test_a_limit_at_the_real_size_still_restores(tmp_path):
    volume = make_volume(tmp_path / "bomb.txt.bv.001", 5 * MIB)
    restored = engine.unpack(volume, tmp_path / "out", max_bytes=5 * MIB)
    assert restored[0].stat().st_size == 5 * MIB


def test_no_limit_by_default(tmp_path):
    volume = make_volume(tmp_path / "big.txt.bv.001", 2 * MIB)
    assert engine.unpack(volume, tmp_path / "out")[0].stat().st_size == 2 * MIB


def test_data_hidden_after_the_end_of_the_archive_is_refused(tmp_path):
    volume = make_volume(tmp_path / "x.txt.bv.001", 100, trailing=20 * MIB)
    with pytest.raises(EngineError, match="unexpected data"):
        engine.test(volume)


def test_manifest_size_caps_what_an_archive_may_expand_to(tmp_path, media):
    bv.pack([media / "notes.txt"], tmp_path / "a", engine="native")
    manifest = tmp_path / "a" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entries"][0]["size"] = 10  # claims a tiny file; the archive holds more
    manifest.write_text(json.dumps(data), encoding="utf-8")

    result = bv.unpack(tmp_path / "a", tmp_path / "out")
    assert result.status == bv.Status.FAILED
    assert "allowed size" in result.entries[0].message
    assert not (tmp_path / "out" / "notes.txt").exists()


def test_max_output_size_option(tmp_path, media):
    bv.pack([media / "notes.txt"], tmp_path / "a", engine="native")

    refused = bv.unpack(tmp_path / "a", tmp_path / "out", max_output_size="1k")
    assert refused.status == bv.Status.FAILED

    allowed = bv.unpack(tmp_path / "a", tmp_path / "out", max_output_size="1m")
    assert allowed.status == bv.Status.SUCCESS


def test_max_output_size_on_the_command_line(tmp_path, media):
    assert run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
               "--engine", "native", "-q") == 0  # fmt: skip

    out = str(tmp_path / "out")
    assert run("unpack", str(tmp_path / "a"), "-o", out, "-q",
               "--max-output-size", "1k") == 1  # fmt: skip
    assert not (tmp_path / "out" / "notes.txt").exists()
    assert run("unpack", str(tmp_path / "a"), "-o", out, "-q",
               "--max-output-size", "10m") == 0  # fmt: skip


# --- owner-only files --------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_archives_and_manifest_are_private_to_the_owner(tmp_path, media):
    previous = os.umask(0o022)  # a typical umask would have made them 0644
    try:
        result = bv.pack([media / "notes.txt"], tmp_path / "a", engine="native",
                         volume_size="2k")  # fmt: skip
    finally:
        os.umask(previous)

    for volume in result.entries[0].volumes:
        assert stat.S_IMODE(volume.stat().st_mode) == 0o600
    assert stat.S_IMODE(result.manifest.stat().st_mode) == 0o600
    assert not list((tmp_path / "a").glob("*.tmp"))


# --- the GitHub Action keeps options and paths apart -------------------------


def test_action_passes_paths_after_a_double_dash():
    text = (REPO / "action.yml").read_text(encoding="utf-8")

    assert 'batchvault "$COMMAND" "${args[@]}" -- "${positional[@]}"' in text
    assert 'positional+=("$line")' in text
    assert 'positional+=("$SOURCE")' in text
    assert 'args+=("$line")' not in text
    assert 'args+=("$SOURCE")' not in text
    assert "::add-mask::" in text


def test_a_path_that_looks_like_an_option_is_just_a_path(tmp_path, media):
    odd = media / "-q"
    odd.write_text("a file whose name looks like an option")

    code = main([
        "pack", "--json", "--engine", "native", "-o", str(tmp_path / "a"),
        "--no-log", "--", str(odd),
    ])  # fmt: skip
    assert code == 0
    assert (tmp_path / "a" / "-q").is_dir()


# --- cleanup does not follow links -------------------------------------------


def test_cleanup_ignores_linked_folders(tmp_path):
    import time

    from batchvault.volumes import cleanup_staging

    elsewhere = tmp_path / "elsewhere"
    victim = elsewhere / ".batchvault-keep"
    victim.mkdir(parents=True)
    long_ago = time.time() - 3 * 86400
    os.utime(victim, (long_ago, long_ago))

    root = tmp_path / "archives"
    root.mkdir()
    try:
        (root / "linked").symlink_to(elsewhere, target_is_directory=True)
        (root / ".batchvault-link").symlink_to(victim, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")

    assert cleanup_staging(root) == 0
    assert victim.exists()
