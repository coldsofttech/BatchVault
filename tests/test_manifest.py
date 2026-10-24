import json

import pytest

from batchvault.errors import ManifestError
from batchvault.manifest import (
    Manifest,
    ManifestEntry,
    VolumeInfo,
    hash_file,
)


def make_entry(name="a.txt", **overrides):
    values = {
        "name": name,
        "size": 10,
        "sha256": "ab" * 32,
        "engine": "native",
        "format": "xz",
        "level": 5,
        "volume_size": 1024,
        "volumes": [VolumeInfo(f"{name}/{name}.bv.001", 5, "cd" * 32)],
    }
    values.update(overrides)
    return ManifestEntry(**values)


def test_save_and_load_roundtrip(tmp_path):
    manifest = Manifest(
        entries={"a.txt": make_entry(), "b é.txt": make_entry("b é.txt")}
    )
    path = tmp_path / "manifest.json"
    manifest.save(path)

    loaded = Manifest.load(path)
    assert loaded.entries == manifest.entries
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_invalid_json(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{nope", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid JSON"):
        Manifest.load(path)


def test_unsupported_schema_version():
    with pytest.raises(ManifestError, match="schema_version"):
        Manifest.from_dict({"schema_version": 99, "entries": []})


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", "..", ""])
def test_unsafe_entry_names_are_rejected(name):
    data = make_entry("ok").to_dict() | {"name": name}
    with pytest.raises(ManifestError, match="unsafe|name"):
        Manifest.from_dict({"schema_version": 1, "entries": [data]})


def test_missing_field_is_reported():
    data = make_entry().to_dict()
    del data["sha256"]
    with pytest.raises(ManifestError, match=r"entries\[0\].*sha256"):
        Manifest.from_dict({"schema_version": 1, "entries": [data]})


def test_duplicate_entries_are_rejected():
    data = make_entry().to_dict()
    with pytest.raises(ManifestError, match="Duplicate"):
        Manifest.from_dict({"schema_version": 1, "entries": [data, data]})


def test_check_volumes(tmp_path):
    volume = tmp_path / "v.001"
    volume.write_bytes(b"hello")
    entry = make_entry(volumes=[VolumeInfo("v.001", 5, hash_file(volume))])
    assert entry.check_volumes(tmp_path) == []

    volume.write_bytes(b"jello")
    assert "checksum" in entry.check_volumes(tmp_path)[0]

    volume.write_bytes(b"hi")
    assert "wrong size" in entry.check_volumes(tmp_path)[0]

    volume.unlink()
    assert "missing" in entry.check_volumes(tmp_path)[0]
