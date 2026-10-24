import pytest

from batchvault.errors import VolumeError
from batchvault.volumes import existing_volumes, find_volumes, split_volume_name


def make_volumes(directory, base, numbers):
    paths = []
    for number in numbers:
        path = directory / f"{base}.{number:03d}"
        path.write_bytes(b"x")
        paths.append(path)
    return paths


def test_split_volume_name():
    assert split_volume_name("a.mp4.7z.003") == ("a.mp4.7z", 3)
    assert split_volume_name("a.bv.001") == ("a.bv", 1)
    assert split_volume_name("a.txt") is None


def test_find_volumes_from_any_volume(tmp_path):
    paths = make_volumes(tmp_path, "intro.mp4.7z", [1, 2, 3])
    assert find_volumes(paths[1]) == paths


def test_find_volumes_ignores_other_sets(tmp_path):
    paths = make_volumes(tmp_path, "a.txt.7z", [1, 2])
    make_volumes(tmp_path, "b.txt.7z", [1])
    assert find_volumes(paths[0]) == paths


def test_find_volumes_detects_gap(tmp_path):
    paths = make_volumes(tmp_path, "a.7z", [1, 3])
    with pytest.raises(VolumeError, match="002"):
        find_volumes(paths[0])


def test_find_volumes_rejects_non_volume(tmp_path):
    with pytest.raises(VolumeError):
        find_volumes(tmp_path / "notes.txt")


def test_existing_volumes_missing_directory(tmp_path):
    assert existing_volumes(tmp_path / "nope", "a.7z") == []


def test_existing_volumes_match_names_in_any_unicode_form(tmp_path):
    composed = "données.bin.7z"
    decomposed = "données.bin.7z"
    (tmp_path / f"{decomposed}.001").write_bytes(b"x")
    (tmp_path / f"{decomposed}.002").write_bytes(b"x")

    found = existing_volumes(tmp_path, composed)

    assert [path.name for path in found] == [f"{decomposed}.001", f"{decomposed}.002"]
