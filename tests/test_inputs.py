import json
import os

import pytest

from batchvault.errors import InputError
from batchvault.filters import PathFilter
from batchvault.inputs import collect_sources, gather_sources, read_list_entries


@pytest.fixture
def files(tmp_path):
    paths = []
    for name in ("a one.txt", "b.mp4", "ünï.csv"):
        path = tmp_path / name
        path.write_text("x", encoding="utf-8")
        paths.append(path)
    return paths


def test_text_list_ignores_blanks_and_comments(tmp_path, files):
    listing = tmp_path / "list.txt"
    listing.write_text(
        f"# comment\n\n{files[0]}\n  {files[1]}  \n", encoding="utf-8-sig"
    )
    assert collect_sources(list_file=listing) == [p.resolve() for p in files[:2]]


def test_json_list_accepts_strings_and_objects(tmp_path, files):
    listing = tmp_path / "list.json"
    listing.write_text(
        json.dumps([str(files[0]), {"path": str(files[2]), "size": 1}]),
        encoding="utf-8",
    )
    assert [e for _, e in read_list_entries(listing)] == [str(files[0]), str(files[2])]


def test_duplicates_are_removed(files):
    sources = collect_sources([files[0], files[0], files[1]])
    assert len(sources) == 2


def test_missing_file_reports_line(tmp_path):
    listing = tmp_path / "list.txt"
    listing.write_text("nope.txt\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"list\.txt:1"):
        collect_sources(list_file=listing)


def test_same_name_in_different_folders_is_fine(tmp_path):
    for folder in ("x", "y"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "same.txt").write_text("1")

    sourced = gather_sources([tmp_path / "x" / "same.txt", tmp_path / "y" / "same.txt"])
    assert [folder for _, folder in sourced] == ["x", "y"]


def test_colliding_places_are_still_refused(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder / "x").mkdir(parents=True)
        (tmp_path / folder / "x" / "same.txt").write_text("1")

    # Two lists recorded different roots, so both files would be stored as x/same.txt.
    listing = tmp_path / "list.json"
    listing.write_text(
        json.dumps([
            {"path": str(tmp_path / "a/x/same.txt"), "root": str(tmp_path / "a")},
            {"path": str(tmp_path / "b/x/same.txt"), "root": str(tmp_path / "b")},
        ]),
        encoding="utf-8",
    )
    with pytest.raises(InputError, match="same archive folder"):
        gather_sources(list_file=listing)


def test_folders_follow_the_recorded_root_then_the_shared_folder(tmp_path):
    for name in ("one/a.txt", "one/two/b.txt", "other.txt"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    one, two = tmp_path / "one" / "a.txt", tmp_path / "one" / "two" / "b.txt"

    shared = dict(gather_sources([one, two]))
    assert shared == {one.resolve(): "", two.resolve(): "two"}

    spread = dict(gather_sources([one, tmp_path / "other.txt"]))
    assert spread == {one.resolve(): "one", (tmp_path / "other.txt").resolve(): ""}

    explicit = dict(gather_sources([one, two], root=tmp_path))
    assert explicit == {one.resolve(): "one", two.resolve(): "one/two"}

    assert dict(gather_sources([one])) == {one.resolve(): ""}


def test_a_recorded_root_in_a_text_list(tmp_path):
    (tmp_path / "Resources" / "intro").mkdir(parents=True)
    (tmp_path / "Resources" / "intro" / "a.txt").write_text("x")
    first = tmp_path / "Resources" / "intro" / "a.txt"
    header = f"# batchvault-root: {tmp_path}"
    lines = [header, str(first)]
    (tmp_path / "list.txt").write_text(os.linesep.join(lines), encoding="utf-8")

    ((_, folder),) = gather_sources(list_file=tmp_path / "list.txt")
    assert folder == "Resources/intro"


def test_an_edited_list_falls_back_to_the_shared_folder(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.txt").write_text("x")
    (tmp_path / "elsewhere").mkdir()

    listing = tmp_path / "list.json"
    item = {"path": str(tmp_path / "data/a.txt"), "root": str(tmp_path / "elsewhere")}
    listing.write_text(json.dumps([item]), encoding="utf-8")
    ((_, folder),) = gather_sources(list_file=listing)
    assert folder == ""  # no error; the recorded root no longer contains the file


def test_archives_are_refused_unless_allowed(tmp_path):
    volume = tmp_path / "a.txt.bv.001"
    volume.write_bytes(b"x")
    with pytest.raises(InputError, match="looks like an archive"):
        collect_sources([volume])
    assert collect_sources([volume], allow_archives=True)


def test_filter_applies(files):
    sources = collect_sources(files, path_filter=PathFilter(exclude=["*.mp4"]))
    assert [p.name for p in sources] == ["a one.txt", "ünï.csv"]


def test_nothing_left_is_an_error(files):
    with pytest.raises(InputError, match="No input files"):
        collect_sources(files, path_filter=PathFilter(include=["*.zip"]))
