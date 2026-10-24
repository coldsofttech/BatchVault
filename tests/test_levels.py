"""--level auto: the level follows the file's type."""

import json
import os

import pytest

import batchvault as bv
from batchvault.cli import main
from batchvault.errors import OptionsError
from batchvault.levels import (
    AUTO,
    BINARY_LEVEL,
    DEFAULT_LEVEL,
    MEDIA_LEVEL,
    PDF_LEVEL,
    TEXT_LEVEL,
    choose_level,
    level_argument,
    parse_level,
)


def make(tmp_path, name, content=b"plain bytes\n" * 100):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# --- the table ----------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["a.mp4", "a.MKV", "a.jpg", "a.png", "a.zip", "a.7z", "a.gz", "a.docx", "a.mp3",
     "a.flac", "a.webp", "a.heic", "a.tar.gz", "a.xlsx", "a.cr2", "a.webm"],
)
def test_already_compressed_files_get_level_zero(tmp_path, name):
    choice = choose_level(make(tmp_path, name))
    assert choice.level == 0 and choice.kind == "already compressed"


@pytest.mark.parametrize(
    "name", ["a.txt", "a.LOG", "a.csv", "a.json", "a.xml", "a.py", "a.sql", "a.md",
             "a.html", "a.yaml", "a.svg"],
)
def test_text_gets_a_strong_level(tmp_path, name):
    choice = choose_level(make(tmp_path, name))
    assert choice.level == TEXT_LEVEL and choice.kind == "text"


@pytest.mark.parametrize("name", ["a.wav", "a.bmp", "a.tiff", "a.TIF"])
def test_uncompressed_media(tmp_path, name):
    assert choose_level(make(tmp_path, name)).level == MEDIA_LEVEL


@pytest.mark.parametrize("name", ["a.exe", "a.iso", "a.sqlite", "a.vmdk", "a.tar"])
def test_binary_data_gets_the_balanced_level(tmp_path, name):
    choice = choose_level(make(tmp_path, name))
    assert choice.level == BINARY_LEVEL and choice.kind == "binary data"


def test_pdf_and_unknown_types(tmp_path):
    assert choose_level(make(tmp_path, "a.pdf")).level == PDF_LEVEL
    for name in ("a.bin", "a.dat", "noextension", "a.weirdext"):
        choice = choose_level(make(tmp_path, name))
        assert choice.level == DEFAULT_LEVEL and choice.kind == "unknown type"


# --- the file's own bytes beat its name ---------------------------------------


@pytest.mark.parametrize(
    ("label", "head"),
    [
        ("zip", b"PK\x03\x04rest"),
        ("gzip", b"\x1f\x8b\x08rest"),
        ("xz", b"\xfd7zXZ\x00rest"),
        ("7z", b"7z\xbc\xaf\x27\x1c rest"),
        ("rar", b"Rar!\x1a\x07\x00rest"),
        ("jpeg", b"\xff\xd8\xff\xe0rest"),
        ("png", b"\x89PNG\r\n\x1a\nrest"),
        ("mp4", b"\x00\x00\x00\x18ftypisom"),
        ("mkv", b"\x1a\x45\xdf\xa3rest"),
        ("ogg", b"OggS\x00rest"),
        ("webp", b"RIFF\x10\x00\x00\x00WEBPVP8 "),
        ("bzip2", b"BZh91AY&SYrest"),
        ("mp3", b"ID3\x03\x00\x00\x00\x00\x00\x10rest"),
    ],
)
def test_a_recognised_signature_means_already_compressed(tmp_path, label, head):
    # The name says text and the extension says nothing; the bytes decide.
    for name in ("notes.txt", "mystery.bin", "noextension"):
        assert choose_level(make(tmp_path, name, head + b"x" * 50)).level == 0, label


def test_text_that_merely_starts_like_a_signature_is_still_text(tmp_path):
    for first_line in (b"BZh is a column\n", b"ID3,name\n", b"PKG,name\n"):
        path = make(tmp_path, "table.csv", first_line * 20)
        assert choose_level(path).level == TEXT_LEVEL, first_line


def test_an_unreadable_or_empty_file_falls_back_to_its_extension(tmp_path):
    assert choose_level(make(tmp_path, "empty.txt", b"")).level == TEXT_LEVEL
    assert choose_level(tmp_path / "missing.log").level == TEXT_LEVEL


# --- parsing ------------------------------------------------------------------


def test_parse_level():
    assert parse_level("auto") == AUTO == parse_level(" AUTO ")
    assert parse_level(7) == 7 and parse_level("7") == 7 and parse_level("0") == 0


@pytest.mark.parametrize("bad", [10, -1, "high", "", 3.5, True])
def test_parse_level_rejects_everything_else(bad):
    with pytest.raises((OptionsError, TypeError)):
        parse_level(bad)


def test_level_argument():
    assert level_argument("Auto") == AUTO
    assert level_argument("9") == 9
    with pytest.raises(Exception, match="invalid level"):
        level_argument("fast")


# --- in a real run ------------------------------------------------------------


@pytest.fixture
def mixed(tmp_path):
    folder = tmp_path / "mixed"
    make(folder, "notes.txt", b"hello world\n" * 3000)
    make(folder, "video.mp4", b"\x00\x00\x00\x18ftypisom" + os.urandom(20_000))
    make(folder, "blob.dat", bytes(range(1, 255)) * 80)  # no signature, no extension
    make(folder, "report.pdf", b"%PDF-1.4\n" + b"text " * 5000)
    return folder


def pack_auto(mixed, tmp_path, **kwargs):
    files = sorted(mixed.iterdir())
    output = tmp_path / "archives"
    return bv.pack(files, output, engine="native", level="auto", **kwargs)


def manifest_levels(tmp_path):
    text = (tmp_path / "archives" / "manifest.json").read_text(encoding="utf-8")
    return {e["name"]: e["level"] for e in json.loads(text)["entries"]}


def test_each_file_gets_the_level_for_its_type(tmp_path, mixed):
    result = pack_auto(mixed, tmp_path)
    assert result.status == bv.Status.SUCCESS

    assert manifest_levels(tmp_path) == {
        "notes.txt": TEXT_LEVEL,
        "video.mp4": 0,
        "blob.dat": DEFAULT_LEVEL,
        "report.pdf": PDF_LEVEL,
    }
    assert {e.name: e.level for e in result.entries} == manifest_levels(tmp_path)


def test_the_result_says_which_level_was_chosen(tmp_path, mixed):
    by_name = {e.name: e for e in pack_auto(mixed, tmp_path).entries}

    assert "level 6 (text)" in by_name["notes.txt"].message
    assert "level 0 (already compressed)" in by_name["video.mp4"].message
    assert "level 5 (unknown type)" in by_name["blob.dat"].message


def test_a_fixed_level_is_unchanged_and_not_annotated(tmp_path, mixed):
    result = bv.pack(sorted(mixed.iterdir()), tmp_path / "archives",
                     engine="native", level=7)  # fmt: skip
    assert set(manifest_levels(tmp_path).values()) == {7}
    assert all("level" not in e.message for e in result.entries)


def test_auto_files_round_trip(tmp_path, mixed):
    pack_auto(mixed, tmp_path)
    restored = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert restored.status == bv.Status.SUCCESS
    for path in mixed.iterdir():
        assert (tmp_path / "out" / path.name).read_bytes() == path.read_bytes()


def test_update_recognises_files_packed_with_auto(tmp_path, mixed):
    pack_auto(mixed, tmp_path)
    again = pack_auto(mixed, tmp_path, update=True)
    assert {e.message for e in again.entries} == {"Up to date."}


def test_a_change_of_type_based_level_repacks_under_update(tmp_path, mixed):
    # Packed with a fixed level first, then auto: the text file's level differs.
    bv.pack(sorted(mixed.iterdir()), tmp_path / "archives", engine="native", level=5)
    result = pack_auto(mixed, tmp_path, update=True)
    states = {e.name: e.status for e in result.entries}

    assert states["notes.txt"] == bv.Status.SUCCESS  # 5 -> 6
    assert states["blob.dat"] == bv.Status.SKIPPED  # unknown type is level 5 already


def test_dry_run_shows_the_plan(tmp_path, mixed):
    result = pack_auto(mixed, tmp_path, dry_run=True)
    by_name = {e.name: e for e in result.entries}

    expected = "Would archive at level 0 (already compressed)."
    assert by_name["video.mp4"].message == expected
    assert by_name["notes.txt"].level == TEXT_LEVEL
    assert not (tmp_path / "archives").exists()


def test_store_ignores_auto(tmp_path, mixed):
    result = pack_auto(mixed, tmp_path, format="store")
    assert all("level" not in e.message for e in result.entries)


def test_invalid_levels_are_refused(tmp_path, mixed):
    for bad in ("fast", 12, -1):
        with pytest.raises(OptionsError):
            bv.pack(sorted(mixed.iterdir()), tmp_path / "a", engine="native", level=bad)


# --- command line and configuration -------------------------------------------


def run(*args):
    return main([*args, "--no-log"])


def test_command_line_accepts_auto(tmp_path, mixed, capsys):
    code = run("pack", str(mixed / "notes.txt"), str(mixed / "video.mp4"), "-o",
               str(tmp_path / "a"), "--engine", "native", "--level", "AUTO",
               "--json", "-q")  # fmt: skip
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert {e["name"]: e["level"] for e in result["entries"]} == {
        "notes.txt": TEXT_LEVEL,
        "video.mp4": 0,
    }


@pytest.mark.parametrize("bad", ["fast", "10", "-1"])
def test_command_line_rejects_other_levels(tmp_path, mixed, bad):
    with pytest.raises(SystemExit) as exc:
        run("pack", str(mixed / "notes.txt"), "-o", str(tmp_path / "a"), "--level", bad)
    assert exc.value.code == 2


def test_config_can_set_auto_and_rejects_nonsense(tmp_path, mixed):
    config = tmp_path / "bv.toml"
    source = str(mixed / "video.mp4")

    config.write_text('[pack]\nengine = "native"\nlevel = "auto"\n')
    assert run("pack", source, "-o", str(tmp_path / "a"), "-q",
               "--config", str(config)) == 0  # fmt: skip
    entry = json.loads((tmp_path / "a" / "manifest.json").read_text("utf-8"))
    assert entry["entries"][0]["level"] == 0

    for bad in ('"fast"', "12", "-1"):
        config.write_text(f'[pack]\nlevel = {bad}\n')
        assert run("pack", source, "-o", str(tmp_path / "b"), "-q",
                   "--config", str(config)) == 2  # fmt: skip
