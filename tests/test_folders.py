"""Folders are kept by default; --root and list files adjust where they start."""

import io
import json
import os
import time

import pytest
from rich.console import Console

import batchvault as bv
from batchvault import ui
from batchvault.cli import main
from batchvault.errors import InputError, ManifestError
from batchvault.volumes import cleanup_staging

FILES = {
    "Resources/intro/lesson1.bin": os.urandom(5000),
    "Resources/intro/notes.txt": b"intro notes\n" * 400,
    "Resources/notes.txt": b"resource notes\n" * 400,  # same name, other folder
    "top.txt": b"top level\n" * 400,
}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "project"
    for name, data in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def sources(tree):
    return [tree / name for name in FILES]


def pack(tree, tmp_path, **kwargs):
    kwargs.setdefault("volume_size", "2k")
    return bv.pack(sources(tree), tmp_path / "archives", engine="native", **kwargs)


def manifest_entries(tmp_path):
    text = (tmp_path / "archives" / "manifest.json").read_text(encoding="utf-8")
    return json.loads(text)["entries"]


def stored(tmp_path):
    return {(e["folder"], e["name"]) for e in manifest_entries(tmp_path)}


# --- the default: folders are kept ------------------------------------------------


def test_folders_are_kept_without_any_option(tmp_path, tree):
    result = pack(tree, tmp_path)
    assert result.status == bv.Status.SUCCESS

    archives = tmp_path / "archives"
    assert (archives / "Resources/intro/lesson1.bin/lesson1.bin.bv.001").is_file()
    assert (archives / "Resources/notes.txt/notes.txt.bv.001").is_file()
    assert (archives / "Resources/intro/notes.txt/notes.txt.bv.001").is_file()
    assert (archives / "top.txt/top.txt.bv.001").is_file()  # directly in the folder

    assert stored(tmp_path) == {
        ("Resources/intro", "lesson1.bin"),
        ("Resources/intro", "notes.txt"),
        ("Resources", "notes.txt"),
        ("", "top.txt"),
    }
    assert {e.folder for e in result.entries} == {"Resources/intro", "Resources", ""}


def test_unpack_recreates_the_tree(tmp_path, tree):
    pack(tree, tmp_path)
    result = bv.unpack(tmp_path / "archives", tmp_path / "out")

    assert result.status == bv.Status.SUCCESS
    for name, data in FILES.items():
        assert (tmp_path / "out" / name).read_bytes() == data
    assert not list((tmp_path / "out").rglob(".batchvault-*"))


def test_files_in_one_folder_are_not_nested(tmp_path, tree):
    both = [tree / "Resources/intro/lesson1.bin", tree / "Resources/intro/notes.txt"]
    bv.pack(both, tmp_path / "archives", engine="native")

    assert (tmp_path / "archives/lesson1.bin/lesson1.bin.bv.001").is_file()
    assert {e["folder"] for e in manifest_entries(tmp_path)} == {""}

    bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert (tmp_path / "out" / "lesson1.bin").is_file()


def test_a_single_file_is_stored_without_folders(tmp_path, tree):
    bv.pack([tree / "Resources/intro/lesson1.bin"], tmp_path / "archives",
            engine="native")  # fmt: skip
    assert stored(tmp_path) == {("", "lesson1.bin")}


def test_same_name_in_two_folders_just_works(tmp_path, tree):
    result = pack(tree, tmp_path)  # two notes.txt, in different folders
    assert result.status == bv.Status.SUCCESS
    bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert (tmp_path / "out/Resources/notes.txt").read_bytes() == (
        FILES["Resources/notes.txt"]
    )
    assert (tmp_path / "out/Resources/intro/notes.txt").read_bytes() == (
        FILES["Resources/intro/notes.txt"]
    )


def test_files_from_different_folders_keep_what_is_below_the_shared_folder(
    tmp_path, tree
):
    other = tmp_path / "other"
    other.mkdir()
    (other / "extra.txt").write_text("extra")
    bv.pack([tree / "top.txt", other / "extra.txt"], tmp_path / "archives",
            engine="native")  # fmt: skip

    # The shared folder is tmp_path itself, so each file keeps its own folder name.
    assert stored(tmp_path) == {("project", "top.txt"), ("other", "extra.txt")}


# --- list files remembers the listed folder ---------------------------------------


@pytest.fixture
def solo(tmp_path):
    """Every file sits one level inside Resources: nothing else would show it."""
    root = tmp_path / "solo"
    for name in ("a.bin", "b.bin"):
        path = root / "Resources" / "intro" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(os.urandom(3000))
    return root


def test_the_listed_folders_name_is_kept(tmp_path, solo):
    listed = bv.list_files(solo / "Resources")
    assert {e.root for e in listed} == {solo.resolve()}

    listing = tmp_path / "files.json"
    listing.write_text(json.dumps([e.to_dict() for e in listed]), encoding="utf-8")
    bv.pack(list_file=listing, output=tmp_path / "archives", engine="native")

    assert stored(tmp_path) == {("Resources/intro", n) for n in ("a.bin", "b.bin")}
    bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert (tmp_path / "out/Resources/intro/a.bin").is_file()


def test_without_the_listing_only_the_shared_folder_is_known(tmp_path, solo):
    files = sorted((solo / "Resources").rglob("*.bin"))
    bv.pack(files, tmp_path / "archives", engine="native")
    assert stored(tmp_path) == {("", "a.bin"), ("", "b.bin")}


def test_text_lists_remember_it_too(tmp_path, solo):
    listing = tmp_path / "files.txt"
    assert main(["list", "files", str(solo / "Resources"), "-o", str(listing), "-q",
                 "--no-log"]) == 0  # fmt: skip
    first_line = listing.read_text(encoding="utf-8").splitlines()[0]
    assert first_line == f"# batchvault-root: {solo.resolve()}"

    bv.pack(list_file=listing, output=tmp_path / "archives", engine="native")
    assert stored(tmp_path) == {("Resources/intro", n) for n in ("a.bin", "b.bin")}


def test_listing_json_on_the_command_line_has_the_root(tmp_path, solo, capsys):
    assert main(["list", "files", str(solo / "Resources"), "--no-log"]) == 0
    items = json.loads(capsys.readouterr().out)
    assert {item["root"] for item in items} == {str(solo.resolve())}


def test_command_line_default_uses_the_listing(tmp_path, tree):
    listing = tmp_path / "files.json"
    assert main(["list", "files", str(tree), "-o", str(listing), "-q", "--no-log"]) == 0

    code = main(["pack", "--list", str(listing), "-o", str(tmp_path / "a"),
                 "--engine", "native", "-q", "--no-log"])  # fmt: skip
    assert code == 0
    first = tmp_path / "a/project/Resources/intro/lesson1.bin/lesson1.bin.bv.001"
    assert first.is_file()

    assert main(["unpack", str(tmp_path / "a"), "-o", str(tmp_path / "o"), "-q",
                 "--no-log"]) == 0  # fmt: skip
    restored = (tmp_path / "o/project/Resources/notes.txt").read_bytes()
    assert restored == FILES["Resources/notes.txt"]


# --- --root overrides ---------------------------------------------------------------


def test_an_explicit_root_decides_where_paths_start(tmp_path, tree):
    inside = [tree / "Resources/intro/lesson1.bin", tree / "Resources/notes.txt"]
    bv.pack(inside, tmp_path / "archives", engine="native", root=tree / "Resources")

    assert stored(tmp_path) == {("intro", "lesson1.bin"), ("", "notes.txt")}


def test_an_explicit_root_keeps_a_chosen_top_folder(tmp_path, tree):
    pack(tree, tmp_path, root=tree.parent)
    assert ("project/Resources/intro", "lesson1.bin") in stored(tmp_path)


def test_a_file_outside_an_explicit_root_is_refused(tmp_path, tree):
    with pytest.raises(InputError, match="not inside the root"):
        pack(tree, tmp_path, root=tree / "Resources/intro")


def test_command_line_root_option(tmp_path, tree):
    listing = tmp_path / "files.json"
    main(["list", "files", str(tree), "-o", str(listing), "-q", "--no-log"])

    code = main(["pack", "--list", str(listing), "--root", str(tree),
                 "-o", str(tmp_path / "a"), "--engine", "native", "-q",
                 "--no-log"])  # fmt: skip
    assert code == 0
    assert (tmp_path / "a/Resources/intro/lesson1.bin/lesson1.bin.bv.001").is_file()


# --- everything else works per path ------------------------------------------------


def test_update_works_per_path_not_per_name(tmp_path, tree):
    pack(tree, tmp_path)
    changed = tree / "Resources/notes.txt"
    changed.write_bytes(b"edited\n" * 400)
    later = time.time() + 30
    os.utime(changed, (later, later))

    result = pack(tree, tmp_path, update=True)
    states = {(e.folder, e.source.name): e.status for e in result.entries}

    assert states[("Resources", "notes.txt")] == bv.Status.SUCCESS
    assert states[("Resources/intro", "notes.txt")] == bv.Status.SKIPPED  # same name
    assert states[("", "top.txt")] == bv.Status.SKIPPED


def test_existing_files_in_the_tree_are_skipped_unless_forced(tmp_path, tree):
    pack(tree, tmp_path)
    bv.unpack(tmp_path / "archives", tmp_path / "out")

    again = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert again.count(bv.Status.SKIPPED) == 4

    forced = bv.unpack(tmp_path / "archives", tmp_path / "out", force=True)
    assert forced.status == bv.Status.SUCCESS


def test_verify_against_compares_the_file_in_the_same_folder(tmp_path, tree):
    pack(tree, tmp_path)
    assert bv.verify(tmp_path / "archives", against=tree).status == bv.Status.SUCCESS

    # Only the intro copy changes; the other notes.txt must not hide that.
    (tree / "Resources/intro/notes.txt").write_bytes(b"different\n")
    result = bv.verify(tmp_path / "archives", against=tree)
    failed = [e for e in result.entries if e.status == bv.Status.FAILED]
    assert [e.folder for e in failed] == ["Resources/intro"]
    assert failed[0].message.startswith("notes.txt differs")


def test_inspect_and_dry_run_show_the_folder(tmp_path, tree):
    plan = pack(tree, tmp_path, dry_run=True)
    assert {e.folder for e in plan.entries} == {"Resources/intro", "Resources", ""}
    assert not (tmp_path / "archives").exists()

    pack(tree, tmp_path)
    inspected = bv.inspect(tmp_path / "archives")
    found = {(e.folder, e.name) for e in inspected}
    assert ("Resources/intro", "notes.txt") in found and ("", "top.txt") in found
    assert all("folder" in e.to_dict() for e in inspected)


def test_without_a_manifest_files_are_restored_flat(tmp_path, tree):
    pack(tree, tmp_path)
    (tmp_path / "archives" / "manifest.json").unlink()

    result = bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert (tmp_path / "out" / "lesson1.bin").is_file()  # the tree is in the manifest
    assert result.warnings


@pytest.mark.parametrize(
    "folder", ["..", "../x", "/abs", "a" + chr(92) + "b", "a//b", "./a", "a/../b", "a/"]
)
def test_unsafe_stored_folders_are_rejected(tmp_path, tree, folder):
    pack(tree, tmp_path)
    manifest = tmp_path / "archives" / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["entries"][0]["folder"] = folder
    manifest.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ManifestError, match="folder"):
        bv.unpack(tmp_path / "archives", tmp_path / "out")
    assert not (tmp_path / "escape").exists()


def test_deeply_nested_leftover_staging_folders_are_cleaned(tmp_path):
    stale = tmp_path / "archives/Resources/intro/file.bin/.batchvault-old"
    fresh = tmp_path / "archives/Resources/.batchvault-new"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    long_ago = time.time() - 3 * 86400
    os.utime(stale, (long_ago, long_ago))

    assert cleanup_staging(tmp_path / "archives") == 1
    assert not stale.exists() and fresh.exists()


def test_the_result_table_shows_the_folder(tmp_path, tree):
    buffer = io.StringIO()
    console = Console(file=buffer, width=160, force_terminal=False, color_system=None)
    ui.print_summary(console, pack(tree, tmp_path), None, "pack")

    assert "Resources/intro/lesson1.bin" in buffer.getvalue()
