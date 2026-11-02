"""Resuming inside a single large file (issue #9)."""

import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

import batchvault as bv
from batchvault import resume as journals
from batchvault.blocks import BlockWriter
from batchvault.checkpoint import FileCheckpoints
from batchvault.engines.native import NativeEngine
from batchvault.resume import PACK_JOURNAL
from batchvault.tarstream import TarStream, normalize
from batchvault.volumes import Adopted, VolumeWriter

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")

BLOCK = 2048
VOLUME = "3k"
PASSWORD = "correct horse battery staple"


# ----------------------------------------------------------- tar stream


def tar_add_bytes(path: Path, name: str) -> bytes:
    """What the engine used to write: ``TarFile.add`` in stream mode."""
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w|", format=tarfile.PAX_FORMAT, bufsize=1 << 20
    ) as tar:
        tar.add(path, arcname=name, filter=normalize)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "name",
    ["a.bin", "x" * 150 + ".txt", "ünïcødé ✓.dat", "with space.bin", "-dash.bin"],
)
@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 1024, 9216, 10240, 70001])
def test_tar_stream_is_byte_identical_to_tar_add(tmp_path, name, size):
    path = tmp_path / name
    path.write_bytes(os.urandom(size))
    os.utime(path, ns=(1_700_000_000_123_456_789,) * 2)

    stream = TarStream(path, name)
    whole = b"".join(stream.chunks())

    assert whole == tar_add_bytes(path, name)
    assert len(whole) == stream.length
    for start in {0, 1, len(stream.header) - 1, len(stream.header),
                  len(stream.header) + 1, stream.length // 2, stream.length - 1,
                  stream.length}:  # fmt: skip
        assert b"".join(stream.chunks(start)) == whole[start:]


def test_tar_stream_rejects_an_offset_outside_the_stream(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x" * 10)
    stream = TarStream(path, "a.bin")
    with pytest.raises(ValueError):
        list(stream.chunks(stream.length + 1))


def test_tar_stream_notices_a_file_that_shrank(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(os.urandom(5000))
    stream = TarStream(path, "a.bin")
    path.write_bytes(b"short")
    with pytest.raises(OSError, match="unexpected end"):
        b"".join(stream.chunks())


# -------------------------------------------------------- volume writer


def test_volume_writer_reports_volumes_and_resumes_identically(tmp_path):
    data = os.urandom(10_000)
    size = 3000
    whole, resumed = tmp_path / "whole", tmp_path / "resumed"
    whole.mkdir()
    resumed.mkdir()

    reported = []
    with VolumeWriter(
        whole, "f.bv", size, on_volume=lambda *a: reported.append(a)
    ) as writer:
        for start in range(0, len(data), 777):
            writer.write(data[start : start + 777])
    assert [(i, s) for i, s, _ in reported] == [(1, size), (2, size), (3, size)]
    first = (whole / "f.bv.001").read_bytes()
    assert reported[0][2] == hashlib.sha256(first).hexdigest()

    # "crash" after 7000 bytes, then carry on from what is on disk
    with VolumeWriter(resumed, "f.bv", size) as writer:
        writer.write(data[:7000])
        position, prefix = writer.sync_state()
    assert position == 7000
    partial = resumed / "f.bv.003"
    assert prefix == hashlib.sha256(partial.read_bytes()).hexdigest()

    adopted = Adopted(
        complete=[resumed / "f.bv.001", resumed / "f.bv.002"],
        partial=partial,
        partial_hash=hashlib.sha256(partial.read_bytes()),
        partial_size=1000,
    )
    with VolumeWriter(resumed, "f.bv", size, adopt=adopted) as writer:
        assert writer.position == 7000
        writer.write(data[7000:])

    for name in ("f.bv.001", "f.bv.002", "f.bv.003", "f.bv.004"):
        assert (resumed / name).read_bytes() == (whole / name).read_bytes()


def test_block_writer_reports_full_blocks_only():
    class Sink(io.BytesIO):
        pass

    done = []
    sink = Sink()
    with BlockWriter(sink, "store", 5, 100, 0, on_block=done.append) as writer:
        writer.write(b"x" * 350)  # three full blocks and a partial one
    assert done == [1, 2, 3]  # the last, partial block is never a checkpoint
    assert len(sink.getvalue()) == 350


def test_block_writer_numbers_blocks_from_start_block():
    done = []
    with BlockWriter(
        io.BytesIO(), "store", 5, 100, 2, start_block=4, on_block=done.append
    ) as writer:
        writer.write(b"x" * 300)
    assert done == [5, 6, 7]


# --------------------------------------------------- end to end (native)


@pytest.fixture(autouse=True)
def small_blocks(monkeypatch):
    """Many small blocks and volumes, so there is plenty to interrupt."""
    monkeypatch.setattr(
        "batchvault.api.get_engine",
        lambda engine, seven_zip=None: NativeEngine(block_size=BLOCK),
    )
    monkeypatch.setattr("batchvault.api.time.sleep", lambda seconds: None)


@pytest.fixture
def big(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    path = folder / "big.bin"
    path.write_bytes(os.urandom(30_000))
    return path


class Chaos:
    """Counts journal events and interrupts at the n-th one."""

    def __init__(self, monkeypatch, at=None, when="after", error=KeyboardInterrupt):
        self.count = 0
        self.at, self.when, self.error = at, when, error
        self.fired = False
        for name in ("volume_done", "checkpoint"):
            monkeypatch.setattr(
                FileCheckpoints, name, self._wrap(getattr(FileCheckpoints, name))
            )

    def _wrap(self, original):
        def wrapper(instance, *args, **kwargs):
            self.count += 1
            hit = self.at == self.count and not self.fired
            if hit and self.when == "before":
                self.fired = True
                raise self.error
            original(instance, *args, **kwargs)
            if hit and self.when == "after":
                self.fired = True
                raise self.error

        return wrapper


class BlockCounter:
    def __init__(self, monkeypatch):
        from batchvault import blocks

        self.count = 0
        original = blocks.compress_block

        def counted(fmt, level, data):
            self.count += 1
            return original(fmt, level, data)

        monkeypatch.setattr(blocks, "compress_block", counted)


def options(fmt="xz", **extra):
    return dict(engine="native", volume_size=VOLUME, format=fmt, level=5, **extra)


def pack_clean(big, out, fmt="xz", **extra):
    return bv.pack([big], out, **options(fmt, **extra))


def volume_fingerprint(folder: Path):
    manifest = json.loads((folder / "manifest.json").read_text())
    return {
        e["name"]: (e["sha256"], [(v["size"], v["sha256"]) for v in e["volumes"]])
        for e in manifest["entries"]
    }


def interrupted(big, out, at, when="after", fmt="xz", **extra):
    with pytest.MonkeyPatch.context() as patch:
        Chaos(patch, at, when)
        with pytest.raises(KeyboardInterrupt):
            bv.pack([big], out, **options(fmt, **extra))
    return out


def event_count(big, tmp_path, fmt="xz", **extra):
    with pytest.MonkeyPatch.context() as patch:
        chaos = Chaos(patch)
        fresh = tmp_path / f"counted-{len(list(tmp_path.glob('counted-*')))}"
        pack_clean(big, fresh, fmt, **extra)
    return chaos.count


def staging_of(out: Path) -> Path:
    return next(out.rglob(".batchvault-resume-*"))


def restored_equal(big, out, tmp_path, **kwargs):
    restored = tmp_path / "restored"
    shutil.rmtree(restored, ignore_errors=True)
    assert bv.unpack(out, restored, **kwargs).status == bv.Status.SUCCESS
    assert (restored / big.name).read_bytes() == big.read_bytes()


@pytest.mark.parametrize("fmt", ["xz", "gzip", "bzip2", "store"])
def test_interrupt_at_every_event_then_resume(tmp_path, big, fmt):
    clean = tmp_path / "clean"
    pack_clean(big, clean, fmt)
    expected = volume_fingerprint(clean)
    events = event_count(big, tmp_path, fmt)
    assert events > 15  # many volumes and blocks to interrupt between

    for at in range(1, events + 1):
        for when in ("before", "after"):
            out = interrupted(big, tmp_path / f"run-{at}-{when}", at, when, fmt)
            assert (out / PACK_JOURNAL).exists()
            assert staging_of(out).is_dir()  # kept for the resume

            result = bv.pack(output=out, resume=True)

            assert result.status == bv.Status.SUCCESS, (at, when)
            assert not list(out.rglob(".batchvault-*"))  # journal and staging gone
            assert bv.verify(out).status == bv.Status.SUCCESS
            # same files, volume layout and checksums as an uninterrupted run
            assert volume_fingerprint(out) == expected, (at, when)


def test_resume_does_not_recompress_finished_blocks(tmp_path, big, monkeypatch):
    with monkeypatch.context() as patch:
        total = BlockCounter(patch)
        pack_clean(big, tmp_path / "clean")
    events = event_count(big, tmp_path)

    out = interrupted(big, tmp_path / "run", int(events * 0.8))
    with monkeypatch.context() as patch:
        counter = BlockCounter(patch)
        result = bv.pack(output=out, resume=True)

    assert 0 < counter.count < total.count * 0.5
    entry = result.entries[0]
    assert entry.continued > 0 and "continued from" in entry.message
    assert not entry.resumed  # the file itself was finished by this run
    restored_equal(big, out, tmp_path)


def test_resume_with_parallel_blocks(tmp_path, big):
    clean = tmp_path / "clean"
    pack_clean(big, clean)
    events = event_count(big, tmp_path, jobs=2)

    for at in (events // 4, events // 2, events - 3):
        out = interrupted(big, tmp_path / f"run{at}", at, jobs=2)
        result = bv.pack(output=out, resume=True)
        assert result.status == bv.Status.SUCCESS
        assert volume_fingerprint(out) == volume_fingerprint(clean)


def test_a_retry_continues_from_the_last_checkpoint(tmp_path, big, monkeypatch):
    events = event_count(big, tmp_path)
    with monkeypatch.context() as patch:
        total = BlockCounter(patch)
        pack_clean(big, tmp_path / "clean")

    with monkeypatch.context() as patch:
        Chaos(
            patch, int(events * 0.8), "after", error=OSError("network drive went away")
        )
        counter = BlockCounter(patch)
        result = bv.pack([big], tmp_path / "out", retries=2, **options())

    assert result.status == bv.Status.SUCCESS
    assert counter.count < total.count * 1.5  # not a full second pass
    assert "continued from" in result.entries[0].message
    assert bv.verify(tmp_path / "out").status == bv.Status.SUCCESS


# ------------------------------------------------------ damage on disk


def mid_run(big, tmp_path, name):
    events = event_count(big, tmp_path)
    return interrupted(big, tmp_path / name, int(events * 0.7))


def staged_volumes(out: Path):
    return sorted(staging_of(out).glob("*.bv.*"))


def baseline_continued(big, tmp_path):
    out = mid_run(big, tmp_path, "baseline")
    return bv.pack(output=out, resume=True).entries[0].continued


def test_garbage_after_the_checkpoint_is_cut_off(tmp_path, big):
    expected = baseline_continued(big, tmp_path)
    out = mid_run(big, tmp_path, "run")
    with staged_volumes(out)[-1].open("ab") as handle:
        handle.write(b"half-written block that was never checkpointed")

    result = bv.pack(output=out, resume=True)

    assert result.entries[0].continued == expected  # nothing was lost
    assert bv.verify(out).status == bv.Status.SUCCESS
    restored_equal(big, out, tmp_path)


@pytest.mark.parametrize(
    "damage", ["flip_first", "flip_partial", "delete_first", "truncate_partial"]
)
def test_damaged_staged_data_is_never_trusted(tmp_path, big, damage):
    expected = baseline_continued(big, tmp_path)
    out = mid_run(big, tmp_path, "run")
    volumes = staged_volumes(out)
    first, partial = volumes[0], volumes[-1]
    assert len(volumes) >= 3

    if damage == "flip_first":
        data = first.read_bytes()
        first.write_bytes(bytes([data[5] ^ 0xFF]).join([data[:5], data[6:]]))
    elif damage == "flip_partial":
        data = partial.read_bytes()
        partial.write_bytes(bytes([data[0] ^ 0xFF]) + data[1:])
    elif damage == "delete_first":
        first.unlink()
    else:
        partial.write_bytes(partial.read_bytes()[:10])

    result = bv.pack(output=out, resume=True)

    assert result.status == bv.Status.SUCCESS
    assert result.entries[0].continued < expected  # fell back or started over
    assert bv.verify(out).status == bv.Status.SUCCESS
    restored_equal(big, out, tmp_path)


def test_missing_staging_folder_starts_the_file_over(tmp_path, big):
    out = mid_run(big, tmp_path, "run")
    shutil.rmtree(staging_of(out))

    result = bv.pack(output=out, resume=True)

    assert result.status == bv.Status.SUCCESS
    assert result.entries[0].continued == 0
    restored_equal(big, out, tmp_path)


def test_changed_source_starts_the_file_over(tmp_path, big):
    out = mid_run(big, tmp_path, "run")
    big.write_bytes(os.urandom(31_000))  # new size and modification time

    result = bv.pack(output=out, resume=True)

    assert result.status == bv.Status.SUCCESS
    assert result.entries[0].continued == 0
    restored_equal(big, out, tmp_path)


def test_touched_source_starts_the_file_over(tmp_path, big):
    out = mid_run(big, tmp_path, "run")
    stat = big.stat()
    os.utime(big, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))

    result = bv.pack(output=out, resume=True)

    assert result.entries[0].continued == 0
    restored_equal(big, out, tmp_path)


def test_progress_does_not_count_continued_bytes_as_speed(tmp_path, big):
    out = mid_run(big, tmp_path, "run")
    events = []

    bv.pack(output=out, resume=True, progress=events.append)

    finish = next(e for e in events if e.kind == "finish")
    assert 0 < finish.skipped_size < finish.size
    assert finish.resumed is False


# ------------------------------------------------------------ encryption


@needs_crypto
def test_encrypted_resume_reuses_the_header(tmp_path, big):
    events = event_count(big, tmp_path, password=PASSWORD)
    with pytest.MonkeyPatch.context() as patch:
        Chaos(patch, int(events * 0.7))
        with pytest.raises(KeyboardInterrupt):
            bv.pack([big], tmp_path / "out", password=PASSWORD, **options())
    out = tmp_path / "out"
    header = staged_volumes(out)[0].read_bytes()[:30]

    result = bv.pack(output=out, resume=True, password=PASSWORD)

    assert result.status == bv.Status.SUCCESS
    assert result.entries[0].continued > 0
    first = next(out.rglob("big.bin.bv.001"))
    assert first.read_bytes()[:30] == header  # same salt and nonce prefix
    assert bv.verify(out, password=PASSWORD).status == bv.Status.SUCCESS
    restored_equal(big, out, tmp_path, password=PASSWORD)


@needs_crypto
def test_encrypted_resume_refuses_another_password(tmp_path, big):
    events = event_count(big, tmp_path, password=PASSWORD)
    out = tmp_path / "out"
    with pytest.MonkeyPatch.context() as patch:
        Chaos(patch, int(events * 0.7))
        with pytest.raises(KeyboardInterrupt):
            bv.pack([big], out, password=PASSWORD, **options())

    result = bv.pack(output=out, resume=True, password="a different password")

    assert result.status == bv.Status.FAILED
    assert "password" in result.entries[0].message
    assert (out / PACK_JOURNAL).exists() and staging_of(out).is_dir()

    fixed = bv.pack(output=out, resume=True, password=PASSWORD)
    assert fixed.status == bv.Status.SUCCESS
    restored_equal(big, out, tmp_path, password=PASSWORD)


@needs_crypto
def test_signed_resume_inside_a_file(tmp_path, big):
    from batchvault import signing

    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    events = event_count(big, tmp_path, sign_key=private)
    out = tmp_path / "out"
    with pytest.MonkeyPatch.context() as patch:
        Chaos(patch, int(events * 0.6))
        with pytest.raises(KeyboardInterrupt):
            bv.pack([big], out, sign_key=private, **options())

    result = bv.pack(output=out, resume=True, sign_key=private)

    assert result.status == bv.Status.SUCCESS
    assert bv.verify(out, public_key=public).status == bv.Status.SUCCESS


# ------------------------------------------------------- journal records


def test_journal_records_volumes_and_checkpoints(tmp_path, big):
    events = event_count(big, tmp_path)
    out = interrupted(big, tmp_path / "run", int(events * 0.7))

    lines = [json.loads(x) for x in (out / PACK_JOURNAL).read_text().splitlines()]
    kinds = {line["kind"] for line in lines[1:]}
    assert kinds == {"volume", "checkpoint"}
    checkpoint = next(line for line in lines if line["kind"] == "checkpoint")
    assert checkpoint["spec"]["block_size"] == BLOCK
    assert checkpoint["blocks"] >= 1 and checkpoint["out"] > 0
    assert "password" not in json.dumps(lines).lower()


def test_loader_groups_records_and_drops_finished_files(tmp_path):
    path = tmp_path / PACK_JOURNAL
    journal = journals.Journal.create(path, "pack", _settings())
    journal.append({"kind": "volume", "key": "a", "index": 1, "size": 9, "sha256": "x"})
    journal.append(_checkpoint("a", 1))
    journal.append(_checkpoint("a", 2))
    journal.append({"kind": "volume", "key": "b", "index": 1, "size": 9, "sha256": "y"})
    journal.append({"kind": "restart", "key": "b"})
    journal.append({"kind": "volume", "key": "c", "index": 1, "size": 9, "sha256": "z"})
    journal.close()

    state = journals.load_pack(path)

    assert state.partial["a"].volumes == {1: (9, "x")}
    assert [c["blocks"] for c in state.partial["a"].checkpoints] == [1, 2]
    assert "b" not in state.partial  # restarted
    assert "c" in state.partial


@pytest.mark.parametrize(
    "record",
    [
        {"kind": "volume", "key": "a", "index": "one", "size": 1, "sha256": "x"},
        {"kind": "checkpoint", "key": "a", "blocks": 1},
        {"kind": "mystery", "key": "a"},
        {"kind": "volume"},
    ],
)
def test_loader_refuses_malformed_records(tmp_path, record):
    path = tmp_path / PACK_JOURNAL
    journal = journals.Journal.create(path, "pack", _settings())
    journal.append(record)
    journal.close()
    with pytest.raises(bv.ResumeError, match="damaged"):
        journals.load_pack(path)


def _settings():
    return journals.PackSettings(
        sources=[("/x/a", "")], engine="native", format="xz", level=5,
        volume_size=1024, jobs=0, retries=1, include=[], exclude=[],
        manifest="/x/manifest.json", update=False, verify=False,
        delete_source=False, strict=False, readme=True, encrypted=False,
        signed=False,
    ).to_dict()  # fmt: skip


def _checkpoint(key, blocks):
    return {
        "kind": "checkpoint", "key": key, "blocks": blocks, "out": blocks * 10,
        "prefix": "", "header": "", "spec": {"block_size": 1},
    }  # fmt: skip
