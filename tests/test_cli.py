import json
import os

import pytest

from batchvault import __version__
from batchvault.cli import main


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "données 1.bin").write_bytes(os.urandom(3000))
    (folder / "notes.txt").write_text("hello " * 200, encoding="utf-8")
    return folder


def run(*args):
    return main([*args, "--no-log"])


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_command_is_required():
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_pack_json_output_and_exit_code(tmp_path, media, capsys):
    code = run(
        "pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "--json", "-q",
    )  # fmt: skip
    assert code == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "success"
    assert result["engine"] == "native"


def test_pack_skipped_exits_1(tmp_path, media):
    args = ["pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"), "-q"]
    assert run(*args, "--engine", "native") == 0
    assert run(*args, "--engine", "native") == 1


def test_pack_without_inputs_is_a_usage_error(tmp_path):
    assert run("pack", "-o", str(tmp_path / "a")) == 2


def test_pack_missing_file_exits_2(tmp_path):
    assert run("pack", str(tmp_path / "nope.txt"), "-o", str(tmp_path / "a")) == 2


def test_pack_from_list_file_then_unpack_and_verify(tmp_path, media):
    listing = tmp_path / "list.txt"
    listing.write_text(f"# files\n{media / 'données 1.bin'}\n", encoding="utf-8")
    archives = tmp_path / "archives"

    assert run("pack", "--list", str(listing), "-o", str(archives), "--engine",
               "native", "--volume-size", "1k", "-q") == 0  # fmt: skip
    assert run("verify", str(archives), "--against", str(media), "-q") == 0
    assert run("unpack", str(archives), "-o", str(tmp_path / "out"), "-q") == 0

    restored = (tmp_path / "out" / "données 1.bin").read_bytes()
    assert restored == (media / "données 1.bin").read_bytes()


def test_pack_include_exclude(tmp_path, media, capsys):
    code = run(
        "pack", str(media / "notes.txt"), str(media / "données 1.bin"),
        "-o", str(tmp_path / "a"), "--engine", "native",
        "--exclude", "*.bin", "--json", "-q",
    )  # fmt: skip
    assert code == 0
    entries = json.loads(capsys.readouterr().out)["entries"]
    assert [e["source"].endswith("notes.txt") for e in entries] == [True]


def test_strict_turns_warning_into_error(tmp_path, media):
    code = run(
        "pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "--format", "bzip2", "--level", "0",
        "--strict", "-q",
    )  # fmt: skip
    assert code == 2


def test_unpack_missing_source_exits_2(tmp_path):
    assert run("unpack", str(tmp_path / "nope"), "-q") == 2


def test_list_files_prints_json(media, capsys):
    assert run("list", "files", str(media), "--exclude", "*.bin") == 0
    listing = json.loads(capsys.readouterr().out)
    assert [item["path"].endswith("notes.txt") for item in listing] == [True]


def test_list_files_to_text_and_json_files(tmp_path, media):
    assert run("list", "files", str(media), "-o", str(tmp_path / "l.txt"), "-q") == 0
    lines = (tmp_path / "l.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# batchvault-root:")
    assert len([line for line in lines if not line.startswith("#")]) == 2

    assert run("list", "files", str(media), "-o", str(tmp_path / "l.json"), "-q") == 0
    assert len(json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))) == 2


def test_list_files_output_feeds_pack(tmp_path, media):
    listing = tmp_path / "l.json"
    run("list", "files", str(media), "-o", str(listing), "-q")
    assert run("pack", "--list", str(listing), "-o", str(tmp_path / "a"),
               "--engine", "native", "-q") == 0  # fmt: skip


def test_list_archives(tmp_path, media, capsys):
    run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "-q")  # fmt: skip
    capsys.readouterr()

    assert run("list", "archives", str(tmp_path / "a")) == 0
    listing = json.loads(capsys.readouterr().out)
    assert listing[0]["path"].endswith("notes.txt.bv.001")


def test_list_files_warns_on_archive_folder(tmp_path, media, capsys):
    run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "native", "-q")  # fmt: skip
    capsys.readouterr()

    run("list", "files", str(tmp_path / "a"))
    assert "list archives" in capsys.readouterr().err


def test_list_archives_warns_on_plain_folder(media, capsys):
    run("list", "archives", str(media))
    captured = capsys.readouterr()
    assert json.loads(captured.out) == []
    assert "list files" in captured.err


def test_doctor_json(capsys):
    assert run("doctor", "--json") == 0
    checks = {c["name"]: c for c in json.loads(capsys.readouterr().out)}
    assert checks["Python"]["ok"]


def test_log_file_is_written(tmp_path, media):
    log = tmp_path / "run.log"
    code = main(["pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
                 "--engine", "native", "-q", "--log-file", str(log)])  # fmt: skip
    assert code == 0
    assert "notes.txt" in log.read_text(encoding="utf-8")


def test_pack_dry_run(tmp_path, media, capsys):
    code = run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
               "--engine", "native", "--dry-run", "--level", "5", "--json")  # fmt: skip
    assert code == 0
    assert json.loads(capsys.readouterr().out)["entries"][0]["message"] == (
        "Would archive."
    )
    assert not (tmp_path / "a").exists()
