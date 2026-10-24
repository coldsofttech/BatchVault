"""End-to-end runs through the public API and CLI with a real 7-Zip."""

import json
import os

import pytest

import batchvault as bv
from batchvault.cli import main
from batchvault.engines.sevenzip import SevenZipEngine

pytestmark = pytest.mark.integration

if not SevenZipEngine().is_available():
    pytest.skip("7-Zip is not installed", allow_module_level=True)


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "données 1.bin").write_bytes(os.urandom(20_000))
    (folder / "notes.txt").write_text("hello " * 2000, encoding="utf-8")
    return folder


def pack(media, tmp_path, **kwargs):
    kwargs.setdefault("volume_size", "4k")
    return bv.pack(
        [media / "données 1.bin", media / "notes.txt"],
        tmp_path / "archives",
        engine="7zip",
        **kwargs,
    )


def test_pack_unpack_verify(tmp_path, media):
    result = pack(media, tmp_path, jobs=2)
    assert result.status == bv.Status.SUCCESS
    assert result.engine == "7zip"

    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert {e["engine"] for e in manifest["entries"]} == {"7zip"}
    assert {e["format"] for e in manifest["entries"]} == {"7z"}

    assert bv.verify(tmp_path / "archives", against=media).status == bv.Status.SUCCESS

    restored = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert restored.status == bv.Status.SUCCESS
    for name in ("données 1.bin", "notes.txt"):
        assert (tmp_path / "out" / name).read_bytes() == (media / name).read_bytes()


def test_unpack_from_any_volume_without_manifest(tmp_path, media):
    pack(media, tmp_path)
    archives = tmp_path / "archives"
    (archives / "manifest.json").unlink()

    volume = sorted((archives / "données 1.bin").glob("*.7z.*"))[2]
    result = bv.unpack(volume, tmp_path / "out")
    assert result.status == bv.Status.SUCCESS
    assert result.warnings
    assert (tmp_path / "out" / "données 1.bin").read_bytes() == (
        media / "données 1.bin"
    ).read_bytes()


def test_missing_last_volume_is_caught(tmp_path, media):
    pack(media, tmp_path)
    folder = tmp_path / "archives" / "données 1.bin"
    sorted(folder.glob("*.7z.*"))[-1].unlink()

    assert bv.verify(tmp_path / "archives").count(bv.Status.FAILED) == 1


def test_update_only_repacks_changed_files(tmp_path, media):
    pack(media, tmp_path)
    assert pack(media, tmp_path, update=True).count(bv.Status.SKIPPED) == 2

    (media / "notes.txt").write_text("changed " * 3000, encoding="utf-8")
    later = (media / "notes.txt").stat().st_mtime + 30
    os.utime(media / "notes.txt", (later, later))

    result = pack(media, tmp_path, update=True)
    assert {e.source.name: e.status for e in result.entries} == {
        "données 1.bin": bv.Status.SKIPPED,
        "notes.txt": bv.Status.SUCCESS,
    }


def test_encrypted_roundtrip(tmp_path, media):
    pack(media, tmp_path, password="s3cret", verify=True)
    archives = tmp_path / "archives"

    assert all(e.encrypted for e in bv.inspect(archives))
    assert bv.verify(archives).count(bv.Status.FAILED) == 2  # no password
    assert bv.verify(archives, password="wrong").count(bv.Status.FAILED) == 2
    assert bv.verify(archives, password="s3cret").status == bv.Status.SUCCESS

    bv.unpack(archives, tmp_path / "out", password="s3cret")
    assert (tmp_path / "out" / "notes.txt").read_bytes() == (
        media / "notes.txt"
    ).read_bytes()


def test_delete_source_after_roundtrip(tmp_path, media):
    original = (media / "notes.txt").read_bytes()
    bv.pack(
        [media / "notes.txt"], tmp_path / "a", engine="7zip", delete_source=True
    )
    assert not (media / "notes.txt").exists()

    bv.unpack(tmp_path / "a", tmp_path / "out")
    assert (tmp_path / "out" / "notes.txt").read_bytes() == original


def test_native_and_7zip_archives_in_one_manifest(tmp_path, media):
    archives = tmp_path / "archives"
    bv.pack([media / "notes.txt"], archives, engine="native")
    bv.pack([media / "données 1.bin"], archives, engine="7zip", volume_size="4k")

    engines = {e.name: e.engine for e in bv.inspect(archives)}
    assert engines == {"notes.txt": "native", "données 1.bin": "7zip"}

    assert bv.unpack(archives, tmp_path / "out").status == bv.Status.SUCCESS


def test_cli_with_7zip(tmp_path, media, capsys):
    code = main([
        "pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
        "--engine", "7zip", "--volume-size", "4k", "--json", "-q", "--no-log",
    ])  # fmt: skip
    assert code == 0
    assert json.loads(capsys.readouterr().out)["engine"] == "7zip"

    assert main(["unpack", str(tmp_path / "a"), "-o", str(tmp_path / "o"), "-q",
                 "--no-log"]) == 0  # fmt: skip


def test_folder_tree_is_kept_with_7zip(tmp_path):
    tree = tmp_path / "tree"
    (tree / "Resources" / "intro").mkdir(parents=True)
    first = tree / "Resources" / "intro" / "notes.txt"
    second = tree / "Resources" / "notes.txt"  # same name, other folder
    first.write_text("intro " * 500)
    second.write_text("resources " * 500)

    result = bv.pack([first, second], tmp_path / "a", engine="7zip", root=tree)
    assert result.status == bv.Status.SUCCESS
    assert (tmp_path / "a/Resources/intro/notes.txt/notes.txt.7z.001").is_file()

    bv.unpack(tmp_path / "a", tmp_path / "out")
    assert (tmp_path / "out/Resources/intro/notes.txt").read_text() == first.read_text()
    assert (tmp_path / "out/Resources/notes.txt").read_text() == second.read_text()
