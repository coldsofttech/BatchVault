"""What the result tables and messages say about sources and archives."""

import io
import os
from pathlib import Path

import pytest
from rich.console import Console

import batchvault as bv
from batchvault import ui
from batchvault.models import FileResult, RunResult, Status
from batchvault.options import format_size


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello " * 5000, encoding="utf-8")
    (folder / "data.bin").write_bytes(os.urandom(30_000))
    return folder


def render(result, kind) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, width=160, force_terminal=False, color_system=None)
    ui.print_summary(console, result, None, kind)
    return buffer.getvalue()


def pack(media, tmp_path, **kwargs):
    return bv.pack(
        [media / "notes.txt", media / "data.bin"],
        tmp_path / "archives",
        engine="native",
        volume_size="8k",
        **kwargs,
    )


def test_pack_messages_say_what_was_made(tmp_path, media):
    result = pack(media, tmp_path, verify=True)
    by_name = {e.source.name: e for e in result.entries}

    notes = by_name["notes.txt"]
    assert notes.archive.name == "notes.txt.bv.001"
    assert notes.stored_size == sum(v.stat().st_size for v in notes.volumes)
    assert notes.message.startswith(f"1 volume, {format_size(30_000)} -> ")
    assert "%)" in notes.message and notes.message.endswith(", verified")

    assert " volumes, " in by_name["data.bin"].message  # random data needs several


def header_of(text: str) -> str:
    return next(line for line in text.splitlines() if "Source" in line)


def assert_columns_in_order(header: str, *names: str) -> None:
    positions = [header.index(name) for name in names]
    assert positions == sorted(positions), header


def test_pack_table_has_source_archive_status_and_details(tmp_path, media):
    text = render(pack(media, tmp_path), "pack")

    assert_columns_in_order(header_of(text), "Source", "Archive", "Status", "Details")
    assert "Pack: source files -> archives" in text
    assert "notes.txt.bv.001" in text and "->" in text


def test_unpack_table_names_the_archive_and_the_restored_file(tmp_path, media):
    pack(media, tmp_path)
    result = bv.unpack(tmp_path / "archives", tmp_path / "out")
    text = render(result, "unpack")

    assert_columns_in_order(header_of(text), "Source", "Archive", "Status", "Details")
    assert "Unpack: archives -> restored files" in text
    assert "notes.txt.bv.001" in text  # the archive
    assert "restored, checksum verified" in text

    # The row names the restored file first and its archive second.
    row = next(line for line in text.splitlines() if "notes.txt.bv.001" in line)
    assert row.index("notes.txt ") < row.index("notes.txt.bv.001")

    entry = next(e for e in result.entries if e.restored[0].name == "notes.txt")
    assert entry.source.name == "notes.txt.bv.001"
    assert entry.to_dict()["restored"] == [str(entry.restored[0])]


def test_verify_says_what_was_checked(tmp_path, media):
    pack(media, tmp_path)

    plain = bv.verify(tmp_path / "archives")
    assert all("checksums and integrity test OK" in e.message for e in plain.entries)

    against = bv.verify(tmp_path / "archives", against=media)
    assert all(e.message.endswith("matches the original") for e in against.entries)

    (tmp_path / "archives" / "manifest.json").unlink()
    bare = bv.verify(tmp_path / "archives")
    assert all("no manifest" in e.message for e in bare.entries)

    text = render(against, "verify")
    assert "Verify: archives" in text
    assert_columns_in_order(header_of(text), "Source", "Archive", "Status", "Details")

    row = next(line for line in text.splitlines() if "notes.txt.bv.001" in line)
    assert row.index("notes.txt ") < row.index("notes.txt.bv.001")
    assert "OK" in row and "matches the original" in row


def test_unpack_without_manifest_says_checksum_not_verified(tmp_path, media):
    pack(media, tmp_path)
    (tmp_path / "archives" / "manifest.json").unlink()

    result = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert all("checksum not verified" in e.message for e in result.entries)


def test_failures_and_skips_keep_their_reason_as_details():
    result = RunResult(
        entries=[
            FileResult(Path("a.txt"), Status.SKIPPED, "archive already exists"),
            FileResult(Path("b.txt"), Status.FAILED, "disk full"),
        ]
    )
    text = render(result, "pack")
    assert "archive already exists" in text and "disk full" in text
    assert "SKIPPED" in text and "FAILED" in text


def test_every_result_names_the_original_file(tmp_path, media):
    packed = pack(media, tmp_path)
    unpacked = bv.unpack(tmp_path / "archives", tmp_path / "out")
    verified = bv.verify(tmp_path / "archives")

    expected = {"notes.txt", "data.bin"}
    for result in (packed, unpacked, verified):
        assert {e.name for e in result.entries} == expected
        assert {e.to_dict()["name"] for e in result.entries} == expected


def test_the_old_one_word_messages_are_gone(tmp_path, media):
    pack(media, tmp_path)
    messages = {
        e.message
        for e in (
            *bv.unpack(tmp_path / "archives", tmp_path / "o").entries,
            *bv.verify(tmp_path / "archives").entries,
        )
    }
    assert not messages & {"ok", "restored"}


# --- inspect uses the same panel, colours and layout ------------------------------


def render_inspect(entries, source=None) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, width=200, force_terminal=False, color_system=None)
    ui.print_inspect(console, entries, source)
    return buffer.getvalue()


def test_inspect_has_a_panel_a_titled_table_and_a_summary(tmp_path, media):
    pack(media, tmp_path)
    text = render_inspect(bv.inspect(tmp_path / "archives"), "archives")

    assert "BatchVault inspect" in text  # the panel
    assert "Archives" in text and "Files" in text and "Size" in text
    assert "Inspect: archives" in text  # the table title

    header = next(line for line in text.splitlines() if "Source" in line)
    for earlier, later in [("Source", "Archive"), ("Archive", "Status"),
                           ("Status", "Engine"), ("Volumes", "Details")]:  # fmt: skip
        assert header.index(earlier) < header.index(later)

    assert "notes.txt.bv.001" in text and " OK " in text
    assert "Summary: 2 archives" in text


def test_inspect_flags_missing_volumes_and_encryption(tmp_path, media):
    pack(media, tmp_path)
    folder = tmp_path / "archives" / "data.bin"
    sorted(folder.glob("*.bv.*"))[-1].unlink()

    text = render_inspect(bv.inspect(tmp_path / "archives"))
    assert "MISSING" in text and "some volumes are missing" in text
    assert "1 with missing volumes" in text


def test_inspect_without_a_manifest_says_what_is_unknown(tmp_path, media):
    pack(media, tmp_path)
    (tmp_path / "archives" / "manifest.json").unlink()

    text = render_inspect(bv.inspect(tmp_path / "archives"))
    assert "no manifest" in text and "MISSING" not in text
