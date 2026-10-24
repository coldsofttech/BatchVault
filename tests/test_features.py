import json
import os
import time

import pytest

import batchvault as bv
from batchvault import api
from batchvault.engines.native import NativeEngine
from batchvault.errors import OptionsError
from batchvault.volumes import cleanup_staging

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "données 1.bin").write_bytes(os.urandom(5000))
    (folder / "notes.txt").write_text("hello " * 500, encoding="utf-8")
    return folder


def pack(media, tmp_path, **kwargs):
    kwargs.setdefault("volume_size", "1k")
    kwargs.setdefault("engine", "native")
    return bv.pack(
        [media / "données 1.bin", media / "notes.txt"], tmp_path / "archives", **kwargs
    )


def touch(path, text):
    path.write_text(text, encoding="utf-8")
    later = time.time() + 30
    os.utime(path, (later, later))


# --- incremental mode -------------------------------------------------------


def test_update_skips_unchanged_and_repacks_changed(tmp_path, media):
    pack(media, tmp_path)

    unchanged = pack(media, tmp_path, update=True)
    assert unchanged.count(bv.Status.SKIPPED) == 2
    assert {e.message for e in unchanged.entries} == {"Up to date."}

    touch(media / "notes.txt", "changed " * 300)
    result = pack(media, tmp_path, update=True)
    by_name = {e.source.name: e for e in result.entries}
    assert by_name["données 1.bin"].status == bv.Status.SKIPPED
    assert by_name["notes.txt"].status == bv.Status.SUCCESS

    restored = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert restored.status == bv.Status.SUCCESS
    assert (tmp_path / "out" / "notes.txt").read_text(encoding="utf-8") == (
        media / "notes.txt"
    ).read_text(encoding="utf-8")


def test_update_repacks_when_settings_change(tmp_path, media):
    pack(media, tmp_path, level=5)
    result = pack(media, tmp_path, update=True, level=9)
    assert result.count(bv.Status.SUCCESS) == 2


def test_update_dry_run_reports_plan(tmp_path, media):
    pack(media, tmp_path)
    touch(media / "notes.txt", "changed " * 300)

    result = pack(media, tmp_path, update=True, dry_run=True, level=5)
    messages = {e.source.name: e.message for e in result.entries}
    assert messages == {
        "données 1.bin": "Would skip: up to date.",
        "notes.txt": "Would update.",
    }


def test_unpack_update(tmp_path, media):
    pack(media, tmp_path)
    source, out = tmp_path / "archives", tmp_path / "out"
    bv.unpack(source, out)

    again = bv.unpack(source, out, update=True)
    assert {e.message for e in again.entries} == {"Already up to date."}

    (out / "notes.txt").write_text("edited")
    assert bv.unpack(source, out).count(bv.Status.SKIPPED) == 2  # no update/force
    assert bv.unpack(source, out, update=True).count(bv.Status.SUCCESS) == 1
    assert (out / "notes.txt").read_text(encoding="utf-8") == (
        media / "notes.txt"
    ).read_text(encoding="utf-8")


# --- retries ----------------------------------------------------------------


@pytest.fixture
def flaky(monkeypatch):
    monkeypatch.setattr(api, "RETRY_BASE_DELAY", 0)
    original = NativeEngine.pack
    state = {"calls": 0, "fail_times": 2, "error": OSError}

    def pack_that_fails(self, source, dest_dir, options):
        state["calls"] += 1
        if state["calls"] <= state["fail_times"]:
            raise state["error"]("transient failure")
        return original(self, source, dest_dir, options)

    monkeypatch.setattr(NativeEngine, "pack", pack_that_fails)
    return state


def test_retries_recover_from_transient_errors(tmp_path, media, flaky):
    result = bv.pack([media / "notes.txt"], tmp_path / "a", engine="native", retries=2)
    assert result.status == bv.Status.SUCCESS
    assert flaky["calls"] == 3


def test_too_few_retries_still_fails(tmp_path, media, flaky):
    result = bv.pack([media / "notes.txt"], tmp_path / "a", engine="native", retries=1)
    assert result.status == bv.Status.FAILED
    assert flaky["calls"] == 2


def test_missing_files_are_not_retried(tmp_path, media, flaky):
    flaky["error"] = FileNotFoundError
    result = bv.pack([media / "notes.txt"], tmp_path / "a", engine="native", retries=5)
    assert result.status == bv.Status.FAILED
    assert flaky["calls"] == 1


def test_negative_retries(tmp_path, media):
    with pytest.raises(OptionsError):
        bv.pack([media / "notes.txt"], tmp_path / "a", engine="native", retries=-1)


# --- inspect ----------------------------------------------------------------


def test_inspect(tmp_path, media):
    pack(media, tmp_path)
    entries = {e.name: e for e in bv.inspect(tmp_path / "archives")}

    big = entries["données 1.bin"]
    assert big.size == 5000 and big.engine == "native" and big.format == "xz"
    assert big.volumes > 1 and big.complete is True and big.encrypted is False
    assert big.ratio is not None
    assert json.dumps(big.to_dict())

    big.path.with_name(big.path.name[:-3] + "002").unlink()
    assert {e.name: e for e in bv.inspect(tmp_path / "archives")}[
        "données 1.bin"
    ].complete is False


def test_inspect_without_manifest(tmp_path, media):
    pack(media, tmp_path)
    (tmp_path / "archives" / "manifest.json").unlink()

    entries = {e.name: e for e in bv.inspect(tmp_path / "archives")}
    assert entries["notes.txt"].engine == "native"
    assert entries["notes.txt"].size is None and entries["notes.txt"].volumes >= 1


# --- safe source cleanup ----------------------------------------------------


def test_delete_source_after_roundtrip(tmp_path, media):
    original = (media / "notes.txt").read_bytes()
    asked = []

    def confirm(paths):
        asked.append(paths)
        return True

    result = bv.pack(
        [media / "notes.txt"], tmp_path / "a", engine="native",
        delete_source=True, confirm=confirm,
    )  # fmt: skip
    assert result.status == bv.Status.SUCCESS
    assert asked == [[(media / "notes.txt").resolve()]]
    assert not (media / "notes.txt").exists()
    assert "original deleted" in result.entries[0].message

    bv.unpack(tmp_path / "a", tmp_path / "out")
    assert (tmp_path / "out" / "notes.txt").read_bytes() == original


def test_declined_confirmation_keeps_originals(tmp_path, media):
    result = bv.pack(
        [media / "notes.txt"], tmp_path / "a", engine="native",
        delete_source=True, confirm=lambda paths: False,
    )  # fmt: skip
    assert (media / "notes.txt").exists()
    assert any("not confirmed" in w for w in result.warnings)


def test_failed_roundtrip_keeps_original(tmp_path, media, monkeypatch):
    monkeypatch.setattr(api, "_roundtrip_problem", lambda *args: "it does not match")
    result = bv.pack(
        [media / "notes.txt"], tmp_path / "a", engine="native", delete_source=True
    )
    assert (media / "notes.txt").exists()
    assert "Original kept" in result.entries[0].warnings[0]


def test_nothing_is_deleted_without_the_flag(tmp_path, media):
    bv.pack([media / "notes.txt"], tmp_path / "a", engine="native")
    assert (media / "notes.txt").exists()


# --- leftovers from killed runs ---------------------------------------------


def test_stale_staging_folders_are_cleaned(tmp_path):
    root = tmp_path / "archives"
    stale = root / "file.bin" / ".batchvault-old"
    fresh = root / "file.bin" / ".batchvault-new"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    long_ago = time.time() - 3 * 86400
    os.utime(stale, (long_ago, long_ago))

    assert cleanup_staging(root) == 1
    assert not stale.exists() and fresh.exists()


# --- encryption through the API ---------------------------------------------


@needs_crypto
def test_encrypted_pack_unpack_verify_inspect(tmp_path, media):
    pack(media, tmp_path, password="pw")
    archives = tmp_path / "archives"

    assert all(e.encrypted for e in bv.inspect(archives))

    assert bv.verify(archives).count(bv.Status.FAILED) == 2
    assert bv.verify(archives, password="pw").status == bv.Status.SUCCESS
    assert bv.verify(archives, password="wrong").count(bv.Status.FAILED) == 2

    missing = bv.unpack(archives, tmp_path / "out")
    assert "password is required" in missing.entries[0].message
    assert not (tmp_path / "out" / "notes.txt").exists()

    bv.unpack(archives, tmp_path / "out", password="pw")
    assert (tmp_path / "out" / "données 1.bin").read_bytes() == (
        media / "données 1.bin"
    ).read_bytes()


@needs_crypto
def test_update_repacks_when_encryption_changes(tmp_path, media):
    pack(media, tmp_path)
    result = pack(media, tmp_path, update=True, password="pw")
    assert result.count(bv.Status.SUCCESS) == 2
