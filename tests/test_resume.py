import json
import os
from pathlib import Path

import pytest

import batchvault as bv
from batchvault import signing
from batchvault.cli import main
from batchvault.resume import PACK_JOURNAL, UNPACK_JOURNAL

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")

PASSWORD = "correct horse battery staple"


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    for name in ("a.bin", "b.bin", "c.bin"):
        (folder / name).write_bytes(os.urandom(6000))
    return folder


def sources(media):
    return [media / "a.bin", media / "b.bin", media / "c.bin"]


def interrupt_after(count):
    """A progress callback that raises Ctrl+C after ``count`` files finished."""
    seen = []

    def progress(event):
        if event.kind == "finish":
            seen.append(event)
            if len(seen) == count:
                raise KeyboardInterrupt

    return progress


def pack_args(media, tmp_path, **extra):
    options = dict(engine="native", volume_size="2k", format="xz", level=5)
    options.update(extra)
    return sources(media), tmp_path / "a", options


def interrupted_pack(media, tmp_path, after=2, **extra):
    files, output, options = pack_args(media, tmp_path, **extra)
    with pytest.raises(KeyboardInterrupt):
        bv.pack(files, output, progress=interrupt_after(after), **options)
    return output


def journal_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def first_volume(entry_dir: Path) -> Path:
    return sorted(entry_dir.glob("*.001"))[0]


# ---------------------------------------------------------------- pack


def test_interrupted_pack_leaves_a_journal(tmp_path, media):
    output = interrupted_pack(media, tmp_path)

    lines = journal_lines(output / PACK_JOURNAL)
    assert lines[0]["kind"] == "header"
    assert lines[0]["settings"]["volume_size"] == 2048
    assert [line["key"] for line in lines[1:]] == ["a.bin"]
    assert not (output / "manifest.json").exists()


def test_journal_never_holds_a_secret(tmp_path, media):
    pytest.importorskip("cryptography")
    files, output, options = pack_args(media, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        bv.pack(
            files, output, password=PASSWORD, progress=interrupt_after(2), **options
        )
    text = (output / PACK_JOURNAL).read_text()
    assert PASSWORD not in text
    assert json.loads(text.splitlines()[0])["settings"]["encrypted"] is True


def test_resume_finishes_the_run_and_cleans_up(tmp_path, media):
    output = interrupted_pack(media, tmp_path)

    result = bv.pack(output=output, resume=True)

    assert result.status == bv.Status.SUCCESS and result.resume
    assert [e.name for e in result.entries] == ["a.bin", "b.bin", "c.bin"]
    assert result.reused == 1 and result.redone == 0
    assert result.entries[0].resumed and not result.entries[1].resumed
    assert "Resumed" in result.entries[0].message
    assert not (output / PACK_JOURNAL).exists()
    assert (output / "BATCHVAULT-README.md").exists()
    assert bv.verify(output).status == bv.Status.SUCCESS

    restored = tmp_path / "r"
    bv.unpack(output, restored)
    for path in sources(media):
        assert (restored / path.name).read_bytes() == path.read_bytes()


def test_resumed_archive_matches_an_uninterrupted_one(tmp_path, media):
    files, _, options = pack_args(media, tmp_path)
    bv.pack(files, tmp_path / "clean", **options)

    output = interrupted_pack(media, tmp_path)
    bv.pack(output=output, resume=True)

    def volumes(folder):
        manifest = json.loads((folder / "manifest.json").read_text())
        return {
            e["name"]: (e["sha256"], [(v["size"], v["sha256"]) for v in e["volumes"]])
            for e in manifest["entries"]
        }

    assert volumes(output) == volumes(tmp_path / "clean")


def test_resume_without_a_journal_is_an_error(tmp_path):
    with pytest.raises(bv.ResumeError, match="Nothing to resume"):
        bv.pack(output=tmp_path / "empty", resume=True)


def test_completed_run_leaves_no_journal(tmp_path, media):
    files, output, options = pack_args(media, tmp_path)
    bv.pack(files, output, **options)
    assert not (output / PACK_JOURNAL).exists()
    with pytest.raises(bv.ResumeError):
        bv.pack(output=output, resume=True)


def test_resume_refuses_settings_given_again(tmp_path, media):
    output = interrupted_pack(media, tmp_path)

    with pytest.raises(bv.OptionsError, match="volume_size"):
        bv.pack(output=output, resume=True, volume_size="1k")
    with pytest.raises(bv.OptionsError, match="files"):
        bv.pack(sources(media), output, resume=True)
    with pytest.raises(bv.OptionsError, match="dry_run"):
        bv.pack(output=output, resume=True, dry_run=True)
    # nothing was changed by the refusals
    assert bv.pack(output=output, resume=True).status == bv.Status.SUCCESS


@pytest.mark.parametrize(
    "damage, reason",
    [
        ("flip", "fails its checksum"),
        ("truncate", "wrong size"),
        ("delete", "missing volume"),
    ],
)
def test_damaged_volume_is_redone_never_trusted(tmp_path, media, damage, reason):
    output = interrupted_pack(media, tmp_path)
    volume = first_volume(output / "a.bin")
    data = volume.read_bytes()
    if damage == "flip":
        volume.write_bytes(bytes([data[0] ^ 0xFF]) + data[1:])
    elif damage == "truncate":
        volume.write_bytes(data[: len(data) // 2])
    else:
        volume.unlink()

    result = bv.pack(output=output, resume=True)

    assert result.status == bv.Status.SUCCESS
    redone = result.entries[0]
    assert redone.redone and reason in redone.redone and not redone.resumed
    assert "redone" in redone.message
    assert result.redone == 1 and result.reused == 0
    assert bv.verify(output).status == bv.Status.SUCCESS


def test_changed_source_is_redone(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    (media / "a.bin").write_bytes(os.urandom(5000))

    result = bv.pack(output=output, resume=True)

    assert result.entries[0].redone == "the source file changed"
    restored = tmp_path / "r"
    bv.unpack(output, restored)
    assert (restored / "a.bin").read_bytes() == (media / "a.bin").read_bytes()


def test_torn_last_journal_line_is_ignored(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    with (output / PACK_JOURNAL).open("a") as handle:
        handle.write('{"kind": "file", "key": "b.bin", "ent')  # died mid-write

    result = bv.pack(output=output, resume=True)

    assert result.status == bv.Status.SUCCESS and result.reused == 1
    assert bv.verify(output).status == bv.Status.SUCCESS


def test_damaged_journal_is_refused(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    path = output / PACK_JOURNAL
    lines = path.read_text().splitlines()
    path.write_text("\n".join([lines[0], "not json", lines[1]]) + "\n")

    with pytest.raises(bv.ResumeError, match="damaged"):
        bv.pack(output=output, resume=True)

    path.write_text("")
    with pytest.raises(bv.ResumeError):
        bv.pack(output=output, resume=True)


def test_journal_from_unpack_is_not_used_for_pack(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    (output / PACK_JOURNAL).rename(output / UNPACK_JOURNAL)
    with pytest.raises(bv.ResumeError):
        bv.pack(output=output, resume=True)


def test_vanished_source_fails_and_keeps_the_journal(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    (media / "c.bin").unlink()

    result = bv.pack(output=output, resume=True)

    assert result.status == bv.Status.PARTIAL
    failed = [e for e in result.entries if e.status == bv.Status.FAILED]
    assert [e.name for e in failed] == ["c.bin"]
    assert (output / PACK_JOURNAL).exists()  # a later --resume can retry

    (media / "c.bin").write_bytes(os.urandom(6000))
    again = bv.pack(output=output, resume=True)
    assert again.status == bv.Status.SUCCESS
    assert not (output / PACK_JOURNAL).exists()


def test_progress_counts_reused_work(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    events = []

    bv.pack(output=output, resume=True, progress=events.append)

    plan = events[0]
    assert plan.kind == "plan" and plan.count == 3 and plan.size == 18000
    finished = {e.source.name: e for e in events if e.kind == "finish"}
    assert finished["a.bin"].resumed is True
    assert finished["b.bin"].resumed is False


def test_resume_with_delete_source_deletes_only_after_a_full_check(tmp_path, media):
    output = interrupted_pack(media, tmp_path, delete_source=True)
    originals = {p.name: p.read_bytes() for p in sources(media)}

    result = bv.pack(output=output, resume=True, confirm=lambda paths: True)

    assert result.status == bv.Status.SUCCESS
    assert not any(p.exists() for p in sources(media))
    restored = tmp_path / "r"
    bv.unpack(output, restored)
    assert {p.name: p.read_bytes() for p in restored.iterdir()} == originals


def test_resume_declined_deletion_keeps_originals(tmp_path, media):
    output = interrupted_pack(media, tmp_path, delete_source=True)

    bv.pack(output=output, resume=True, confirm=lambda paths: False)

    assert all(p.exists() for p in sources(media))


@needs_crypto
def test_resume_encrypted_needs_the_password(tmp_path, media):
    files, output, options = pack_args(media, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        bv.pack(
            files, output, password=PASSWORD, progress=interrupt_after(2), **options
        )

    with pytest.raises(bv.ResumeError, match="password is required"):
        bv.pack(output=output, resume=True)
    with pytest.raises(bv.ResumeError, match="does not match"):
        bv.pack(output=output, resume=True, password="wrong")

    result = bv.pack(output=output, resume=True, password=PASSWORD)
    assert result.status == bv.Status.SUCCESS and result.reused == 1
    assert bv.verify(output, password=PASSWORD).status == bv.Status.SUCCESS
    restored = tmp_path / "r"
    bv.unpack(output, restored, password=PASSWORD)
    for path in sources(media):
        assert (restored / path.name).read_bytes() == path.read_bytes()


@needs_crypto
def test_resume_rejects_a_password_for_a_plain_run(tmp_path, media):
    output = interrupted_pack(media, tmp_path)
    with pytest.raises(bv.ResumeError, match="not encrypted"):
        bv.pack(output=output, resume=True, password=PASSWORD)


@needs_crypto
def test_resume_signed_needs_the_key(tmp_path, media):
    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    output = interrupted_pack(media, tmp_path, sign_key=private)

    with pytest.raises(bv.ResumeError, match="private key is required"):
        bv.pack(output=output, resume=True)

    result = bv.pack(output=output, resume=True, sign_key=private)
    assert result.status == bv.Status.SUCCESS
    assert (output / "manifest.json.sig").exists()
    assert bv.verify(output, public_key=public).status == bv.Status.SUCCESS


@needs_crypto
def test_resume_rejects_a_key_for_an_unsigned_run(tmp_path, media):
    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    output = interrupted_pack(media, tmp_path)
    with pytest.raises(bv.ResumeError, match="did not sign"):
        bv.pack(output=output, resume=True, sign_key=private)


# -------------------------------------------------------------- unpack


@pytest.fixture
def archive(tmp_path, media):
    files, output, options = pack_args(media, tmp_path)
    bv.pack(files, output, **options)
    return output


def interrupted_unpack(archive, tmp_path, after=2, **extra):
    restored = tmp_path / "restored"
    with pytest.raises(KeyboardInterrupt):
        bv.unpack(archive, restored, progress=interrupt_after(after), **extra)
    return restored


def test_interrupted_unpack_leaves_a_journal(tmp_path, archive):
    restored = interrupted_unpack(archive, tmp_path)

    lines = journal_lines(restored / UNPACK_JOURNAL)
    assert lines[0]["command"] == "unpack"
    assert [line["path"] for line in lines[1:]] == ["a.bin", "b.bin"]
    assert not (restored / "c.bin").exists()


def test_unpack_resume_finishes_and_cleans_up(tmp_path, media, archive):
    restored = interrupted_unpack(archive, tmp_path)

    result = bv.unpack(output=restored, resume=True)

    assert result.status == bv.Status.SUCCESS and result.resume
    assert result.reused == 2 and result.redone == 0
    assert [e.resumed for e in result.entries] == [True, True, False]
    assert not (restored / UNPACK_JOURNAL).exists()
    for path in sources(media):
        assert (restored / path.name).read_bytes() == path.read_bytes()


def test_unpack_resume_without_a_journal_is_an_error(tmp_path):
    with pytest.raises(bv.ResumeError, match="Nothing to resume"):
        bv.unpack(output=tmp_path / "nowhere", resume=True)


def test_completed_unpack_leaves_no_journal(tmp_path, archive):
    restored = tmp_path / "restored"
    bv.unpack(archive, restored)
    assert not (restored / UNPACK_JOURNAL).exists()


def test_unpack_resume_refuses_source_force_and_update(tmp_path, archive):
    restored = interrupted_unpack(archive, tmp_path)
    for extra in ({"source": archive}, {"force": True}, {"update": True}):
        with pytest.raises(bv.OptionsError, match="resume takes every setting"):
            bv.unpack(output=restored, resume=True, **extra)


def test_unpack_requires_a_source_without_resume(tmp_path):
    with pytest.raises(bv.OptionsError, match="source is required"):
        bv.unpack(output=tmp_path / "x")


def test_unpack_redoes_a_file_it_wrote_that_no_longer_matches(tmp_path, media, archive):
    restored = interrupted_unpack(archive, tmp_path)
    (restored / "a.bin").write_bytes(b"cut short")  # ours (journaled), now bad

    result = bv.unpack(output=restored, resume=True)

    assert result.status == bv.Status.SUCCESS
    assert result.entries[0].redone and not result.entries[0].resumed
    assert result.redone == 1 and result.reused == 1
    assert (restored / "a.bin").read_bytes() == (media / "a.bin").read_bytes()


def test_unpack_never_touches_unrelated_files(tmp_path, media, archive):
    restored = interrupted_unpack(archive, tmp_path)
    unrelated = restored / "notes.txt"
    unrelated.write_text("mine")
    lookalike = restored / "c.bin"  # same name as a file still to restore
    lookalike.write_bytes(b"someone else's file")

    result = bv.unpack(output=restored, resume=True)

    assert unrelated.read_text() == "mine"
    assert lookalike.read_bytes() == b"someone else's file"
    skipped = [e for e in result.entries if e.status == bv.Status.SKIPPED]
    assert [e.name for e in skipped] == ["c.bin"]
    assert "left alone" in skipped[0].message
    assert result.status == bv.Status.PARTIAL


def test_unpack_resume_refuses_a_changed_manifest(tmp_path, media, archive):
    restored = interrupted_unpack(archive, tmp_path)
    files, _, options = pack_args(media, tmp_path)
    (media / "a.bin").write_bytes(os.urandom(100))
    bv.pack(files, archive, force=True, **options)

    with pytest.raises(bv.ResumeError, match="manifest"):
        bv.unpack(output=restored, resume=True)


def test_unpack_resume_keeps_the_journal_while_something_failed(
    tmp_path, media, archive
):
    restored = interrupted_unpack(archive, tmp_path)
    volume = first_volume(archive / "c.bin")
    volume.write_bytes(b"x" + volume.read_bytes()[1:])  # corrupt what is left

    result = bv.unpack(output=restored, resume=True)

    assert result.status == bv.Status.PARTIAL
    assert (restored / UNPACK_JOURNAL).exists()
    assert not (restored / "c.bin").exists()  # nothing unverified was placed


@needs_crypto
def test_unpack_resume_encrypted_and_signed(tmp_path, media):
    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    files, output, options = pack_args(media, tmp_path)
    bv.pack(files, output, password=PASSWORD, sign_key=private, **options)

    restored = tmp_path / "restored"
    with pytest.raises(KeyboardInterrupt):
        bv.unpack(
            output, restored, password=PASSWORD, public_key=public,
            progress=interrupt_after(2),
        )  # fmt: skip

    with pytest.raises(bv.ResumeError, match="public key is required"):
        bv.unpack(output=restored, resume=True, password=PASSWORD)

    result = bv.unpack(
        output=restored, resume=True, password=PASSWORD, public_key=public
    )
    assert result.status == bv.Status.SUCCESS and result.reused == 2
    for path in sources(media):
        assert (restored / path.name).read_bytes() == path.read_bytes()


# ----------------------------------------------------------------- CLI


def run(*args):
    return main([*args, "--no-log", "-q"])


def test_cli_pack_resume_needs_only_the_output(tmp_path, media, capsys):
    output = interrupted_pack(media, tmp_path)

    assert main(["pack", "--resume", "-o", str(output), "--no-log", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["resume"] == {"reused": 1, "redone": 0}
    assert result["entries"][0]["resumed"] is True
    assert not (output / PACK_JOURNAL).exists()


def test_cli_pack_resume_refuses_settings(tmp_path, media, capsys):
    output = interrupted_pack(media, tmp_path)

    code = run("pack", "--resume", "-o", str(output), "--level", "5")
    assert code == 2
    assert "--level" in capsys.readouterr().err
    assert run("pack", "--resume", "-o", str(output), "--volume-size=1k") == 2
    assert run("pack", str(media / "a.bin"), "--resume", "-o", str(output)) == 2
    assert (output / PACK_JOURNAL).exists()  # untouched


def test_cli_pack_resume_without_a_journal(tmp_path, capsys):
    assert run("pack", "--resume", "-o", str(tmp_path / "none")) == 2
    assert "Nothing to resume" in capsys.readouterr().err


def test_cli_resume_settings_file_values_do_not_count_as_given(
    tmp_path, media, monkeypatch
):
    output = interrupted_pack(media, tmp_path)
    (tmp_path / "batchvault.toml").write_text(
        '[pack]\nvolume-size = "7k"\nlevel = 9\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert run("pack", "--resume", "-o", str(output)) == 0
    manifest = json.loads((output / "manifest.json").read_text())
    assert {e["volume_size"] for e in manifest["entries"]} == {2048}  # not 7k


@needs_crypto
def test_cli_pack_resume_encrypted(tmp_path, media, monkeypatch):
    files, output, options = pack_args(media, tmp_path)
    with pytest.raises(KeyboardInterrupt):
        bv.pack(
            files, output, password=PASSWORD, progress=interrupt_after(2), **options
        )

    monkeypatch.delenv("BATCHVAULT_PASSWORD", raising=False)
    assert run("pack", "--resume", "-o", str(output)) == 2

    monkeypatch.setenv("BATCHVAULT_PASSWORD", PASSWORD)
    assert run("pack", "--resume", "-o", str(output)) == 0
    assert bv.verify(output, password=PASSWORD).status == bv.Status.SUCCESS


def test_cli_unpack_resume(tmp_path, media, archive, capsys):
    restored = interrupted_unpack(archive, tmp_path)

    assert main(["unpack", "--resume", "-o", str(restored), "--no-log", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["resume"] == {"reused": 2, "redone": 0}


def test_cli_unpack_resume_refuses_settings(tmp_path, archive):
    restored = interrupted_unpack(archive, tmp_path)
    assert run("unpack", str(archive), "--resume", "-o", str(restored)) == 2
    assert run("unpack", "--resume", "--force", "-o", str(restored)) == 2
    assert run("unpack", "--resume", "-o", str(tmp_path / "none")) == 2


def test_cli_unpack_needs_a_source_without_resume(tmp_path):
    assert run("unpack", "-o", str(tmp_path / "x")) == 2


def test_config_resume_key_is_accepted(tmp_path, media, monkeypatch):
    output = interrupted_pack(media, tmp_path)
    (tmp_path / "batchvault.toml").write_text("[pack]\nresume = true\n")
    monkeypatch.chdir(tmp_path)
    assert run("pack", "-o", str(output)) == 0


# ------------------------------------------------------------------ ETA


def test_eta_does_not_count_reused_work_as_speed():
    from batchvault.eta import EtaEstimator

    now = [0.0]
    eta = EtaEstimator(clock=lambda: now[0])
    eta.start()
    eta.skip(1000)  # an earlier run already did this much
    now[0] = 10.0

    # 100 of the remaining 1000 bytes took 10 s -> 90 s left, not a tiny number
    assert eta.estimate(done_bytes=1100, total_bytes=2000) == pytest.approx(90.0)


def test_failed_unpack_into_a_new_folder_leaves_nothing_behind(tmp_path, archive):
    for volume in archive.glob("*/*.001"):
        volume.write_bytes(b"x" + volume.read_bytes()[1:])  # every archive is bad
    restored = tmp_path / "new-folder"

    result = bv.unpack(archive, restored)

    assert result.status == bv.Status.FAILED
    assert not restored.exists()
