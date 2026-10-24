import json
import os

import pytest

import batchvault as bv
from batchvault import Status
from batchvault.errors import InputError, ManifestError


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "données 1.bin").write_bytes(os.urandom(5000))
    (folder / "notes.txt").write_text("hello " * 500, encoding="utf-8")
    (folder / "skip.log").write_text("log")
    return folder


def pack(media, tmp_path, **kwargs):
    kwargs.setdefault("volume_size", "1k")
    kwargs.setdefault("engine", "native")
    return bv.pack(
        [media / "données 1.bin", media / "notes.txt"], tmp_path / "archives", **kwargs
    )


def test_pack_writes_volumes_and_manifest(tmp_path, media):
    result = pack(media, tmp_path, jobs=2)

    assert result.status == Status.SUCCESS
    assert result.manifest == (tmp_path / "archives" / "manifest.json").resolve()

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    entries = {e["name"]: e for e in manifest["entries"]}
    assert set(entries) == {"données 1.bin", "notes.txt"}
    big = entries["données 1.bin"]
    assert big["engine"] == "native" and big["format"] == "xz"
    assert len(big["volumes"]) > 1
    assert big["volumes"][0]["file"] == "données 1.bin/données 1.bin.bv.001"


def test_existing_archives_are_skipped_then_forced(tmp_path, media):
    pack(media, tmp_path)

    again = pack(media, tmp_path)
    assert again.status == Status.SKIPPED
    assert again.count(Status.SKIPPED) == 2

    assert pack(media, tmp_path, force=True).status == Status.SUCCESS


def test_manifest_is_merged_across_runs(tmp_path, media):
    archives = tmp_path / "archives"
    bv.pack([media / "notes.txt"], archives, engine="native")
    bv.pack([media / "données 1.bin"], archives, engine="native")

    manifest = json.loads((archives / "manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["entries"]) == 2


def test_failed_file_does_not_stop_others(tmp_path, media):
    source = media / "notes.txt"
    result = bv.pack([source, media / "données 1.bin"], tmp_path / "a", engine="native")
    assert result.status == Status.SUCCESS

    # A directory in place of an output folder makes just that file fail.
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "notes.txt").write_text("blocks the output folder")
    result = bv.pack([source, media / "données 1.bin"], tmp_path / "b", engine="native")
    assert result.status == Status.PARTIAL
    assert result.count(Status.FAILED) == 1


@pytest.mark.parametrize("how", ["manifest", "folder", "volume", "list"])
def test_unpack_sources(tmp_path, media, how):
    pack(media, tmp_path)
    archives = tmp_path / "archives"

    if how == "manifest":
        source = archives / "manifest.json"
    elif how == "folder":
        source = archives
    elif how == "volume":
        source = archives / "notes.txt" / "notes.txt.bv.001"
    else:
        source = tmp_path / "unarchive.txt"
        source.write_text("archives/notes.txt/notes.txt.bv.001\n", encoding="utf-8")

    result = bv.unpack(source, tmp_path / "restored")
    assert result.status == Status.SUCCESS
    assert (tmp_path / "restored" / "notes.txt").read_text(
        encoding="utf-8"
    ) == (media / "notes.txt").read_text(encoding="utf-8")
    assert not [p for p in (tmp_path / "restored").iterdir() if p.name.startswith(".")]


def test_unpack_one_subfolder_only_restores_that_archive(tmp_path, media):
    pack(media, tmp_path)
    result = bv.unpack(tmp_path / "archives" / "notes.txt", tmp_path / "restored")

    assert [e.source.name for e in result.entries] == ["notes.txt.bv.001"]
    assert not (tmp_path / "restored" / "données 1.bin").exists()


def test_unpack_restores_all_content(tmp_path, media):
    pack(media, tmp_path, jobs=3)
    result = bv.unpack(tmp_path / "archives", tmp_path / "restored")

    assert result.status == Status.SUCCESS
    for name in ("données 1.bin", "notes.txt"):
        restored = (tmp_path / "restored" / name).read_bytes()
        assert restored == (media / name).read_bytes()


def test_unpack_skips_existing_unless_forced(tmp_path, media):
    pack(media, tmp_path)
    source = tmp_path / "archives"
    bv.unpack(source, tmp_path / "restored")

    assert bv.unpack(source, tmp_path / "restored").status == Status.SKIPPED
    assert bv.unpack(source, tmp_path / "restored", force=True).status == Status.SUCCESS


def test_corrupt_volume_fails_manifest_check(tmp_path, media):
    pack(media, tmp_path)
    volume = tmp_path / "archives" / "données 1.bin" / "données 1.bin.bv.002"
    data = bytearray(volume.read_bytes())
    data[10] ^= 0xFF
    volume.write_bytes(bytes(data))

    result = bv.unpack(tmp_path / "archives", tmp_path / "restored")
    assert result.status == Status.PARTIAL
    failed = next(e for e in result.entries if e.status == Status.FAILED)
    assert "checksum" in failed.message
    assert not (tmp_path / "restored" / "données 1.bin").exists()


def test_missing_last_volume_is_caught_by_manifest(tmp_path, media):
    pack(media, tmp_path)
    folder = tmp_path / "archives" / "données 1.bin"
    sorted(folder.glob("*.bv.*"))[-1].unlink()

    result = bv.verify(tmp_path / "archives" / "manifest.json")
    assert result.count(Status.FAILED) == 1
    assert "missing volume" in next(
        e.message for e in result.entries if e.status == Status.FAILED
    )


def test_unpack_without_manifest_warns(tmp_path, media):
    pack(media, tmp_path)
    (tmp_path / "archives" / "manifest.json").unlink()

    result = bv.unpack(tmp_path / "archives", tmp_path / "restored")
    assert result.status == Status.SUCCESS
    assert result.warnings


def test_verify_against_originals(tmp_path, media):
    pack(media, tmp_path)
    source = tmp_path / "archives"

    assert bv.verify(source, against=media).status == Status.SUCCESS

    (media / "notes.txt").write_text("changed")
    result = bv.verify(source, against=media)
    assert result.count(Status.FAILED) == 1


def test_damaged_manifest_stops_pack_before_any_work(tmp_path, media):
    archives = tmp_path / "archives"
    archives.mkdir()
    (archives / "manifest.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(ManifestError):
        bv.pack([media / "notes.txt"], archives, engine="native")
    assert not (archives / "notes.txt").exists()


def test_progress_events(tmp_path, media):
    events = []
    pack(media, tmp_path, progress=events.append)
    kinds = sorted(e.kind for e in events)
    assert kinds == ["finish", "finish", "plan", "start", "start"]
    plan = next(e for e in events if e.kind == "plan")
    assert plan.count == 2
    assert plan.size == sum(
        (media / name).stat().st_size for name in ("données 1.bin", "notes.txt")
    )


def test_list_files_with_filters(media):
    found = bv.list_files(media, exclude=["*.log"])
    assert [f.path.name for f in found] == ["données 1.bin", "notes.txt"]
    assert found[1].size == (media / "notes.txt").stat().st_size
    assert [f.path.name for f in bv.list_files(media, include=["*.log"])] == [
        "skip.log"
    ]


def test_list_archives(tmp_path, media):
    pack(media, tmp_path)
    found = bv.list_archives(tmp_path / "archives")
    assert sorted(f.path.name for f in found) == [
        "données 1.bin.bv.001",
        "notes.txt.bv.001",
    ]
    assert bv.list_archives(media) == []


def test_unknown_engine(tmp_path, media):
    with pytest.raises(InputError, match="Unknown engine"):
        bv.pack([media / "notes.txt"], tmp_path / "a", engine="zip")


def test_result_is_json_serialisable(tmp_path, media):
    result = pack(media, tmp_path)
    assert json.loads(json.dumps(result.to_dict()))["status"] == "success"


def test_dry_run_writes_nothing(tmp_path, media):
    result = pack(media, tmp_path, dry_run=True, level=5)

    assert result.status == Status.SUCCESS
    assert [e.message for e in result.entries] == ["Would archive."] * 2
    assert result.manifest is None
    assert not (tmp_path / "archives").exists()


def test_dry_run_reports_existing_archives(tmp_path, media):
    pack(media, tmp_path)
    result = pack(media, tmp_path, dry_run=True)
    assert result.count(Status.SKIPPED) == 2
    assert pack(media, tmp_path, dry_run=True, force=True).status == Status.SUCCESS


def test_verify_against_finds_originals_in_subfolders(tmp_path):
    tree = tmp_path / "tree"
    (tree / "sub dir").mkdir(parents=True)
    nested = tree / "sub dir" / "nested.txt"
    nested.write_text("nested " * 100)

    bv.pack([nested], tmp_path / "a", engine="native")
    assert bv.verify(tmp_path / "a", against=tree).status == Status.SUCCESS

    nested.write_text("changed")
    assert bv.verify(tmp_path / "a", against=tree).status == Status.FAILED
