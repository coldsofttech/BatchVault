import io
import os
import tarfile

import pytest

from batchvault.blocks import sniff_format
from batchvault.engines.native import NativeEngine, _check_member
from batchvault.errors import EngineError, OutputExistsError, VolumeError
from batchvault.options import PackOptions

# Small blocks and volumes so every test spans several of each.
engine = NativeEngine(block_size=4096)

COMPRESSED = ["xz", "gzip", "bzip2"]


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "données 1.bin"
    path.write_bytes(os.urandom(20_000))
    return path


def pack(source, tmp_path, **kwargs):
    kwargs.setdefault("volume_size", 3000)
    return engine.pack(source, tmp_path / "archives", PackOptions(**kwargs))


@pytest.mark.parametrize("fmt", ["xz", "gzip", "bzip2", "store"])
def test_roundtrip(tmp_path, source, fmt):
    volumes = pack(source, tmp_path, format=fmt, jobs=3)

    assert len(volumes) > 1
    assert volumes[0].name == "données 1.bin.bv.001"
    assert sniff_format(volumes[0]) == fmt

    engine.test(volumes[0])

    restored = engine.unpack(volumes[-1], tmp_path / "restored")
    assert [path.name for path in restored] == [source.name]
    assert restored[0].read_bytes() == source.read_bytes()


def test_compressible_data_shrinks(tmp_path):
    source = tmp_path / "zeros.bin"
    source.write_bytes(b"\0" * 100_000)
    volumes = pack(source, tmp_path, volume_size=10_000_000)
    assert len(volumes) == 1
    assert volumes[0].stat().st_size < 5_000


def test_empty_file(tmp_path):
    source = tmp_path / "empty.txt"
    source.write_bytes(b"")
    volumes = pack(source, tmp_path)
    restored = engine.unpack(volumes[0], tmp_path / "restored")
    assert restored[0].read_bytes() == b""


@pytest.mark.parametrize("fmt", ["xz", "gzip", "bzip2"])
def test_joined_volumes_are_a_standard_tar(tmp_path, source, fmt):
    volumes = pack(source, tmp_path, format=fmt)
    joined = b"".join(volume.read_bytes() for volume in volumes)

    with tarfile.open(fileobj=io.BytesIO(joined), mode="r:*") as tar:
        member = tar.getmembers()[0]
        assert member.name == source.name
        assert tar.extractfile(member).read() == source.read_bytes()


def test_existing_archive_is_not_replaced_without_force(tmp_path, source):
    pack(source, tmp_path)

    with pytest.raises(OutputExistsError):
        pack(source, tmp_path)

    pack(source, tmp_path, force=True)


def test_force_removes_stale_volumes(tmp_path, source):
    first = pack(source, tmp_path, volume_size=1000)
    second = pack(source, tmp_path, volume_size=1_000_000, force=True)

    assert len(second) == 1
    assert not any(volume.exists() for volume in first[1:])


def test_unpack_does_not_overwrite_without_force(tmp_path, source):
    volumes = pack(source, tmp_path)
    output = tmp_path / "restored"
    engine.unpack(volumes[0], output)

    with pytest.raises(OutputExistsError):
        engine.unpack(volumes[0], output)

    engine.unpack(volumes[0], output, force=True)


def test_no_staging_folders_left_behind(tmp_path, source):
    volumes = pack(source, tmp_path)
    engine.unpack(volumes[0], tmp_path / "restored")

    for folder in (tmp_path / "archives", tmp_path / "restored"):
        assert not [p for p in folder.iterdir() if p.name.startswith(".batchvault-")]


@pytest.mark.parametrize("fmt", COMPRESSED)
def test_corrupt_data_is_detected(tmp_path, source, fmt):
    volumes = pack(source, tmp_path, format=fmt)
    data = bytearray(volumes[1].read_bytes())
    data[len(data) // 2] ^= 0xFF
    volumes[1].write_bytes(bytes(data))

    with pytest.raises(EngineError):
        engine.test(volumes[0])


@pytest.mark.parametrize("fmt", COMPRESSED)
def test_truncated_archive_is_detected(tmp_path, source, fmt):
    volumes = pack(source, tmp_path, format=fmt)
    last = volumes[-1]
    last.write_bytes(last.read_bytes()[:-1])

    with pytest.raises(EngineError):
        engine.test(volumes[0])


def test_missing_middle_volume_is_detected(tmp_path, source):
    volumes = pack(source, tmp_path)
    volumes[1].unlink()

    with pytest.raises(VolumeError):
        engine.unpack(volumes[0], tmp_path / "restored")


def test_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        pack(tmp_path / "nope.txt", tmp_path)


def test_unsafe_archive_entry_is_rejected(tmp_path):
    archive = tmp_path / "evil.bv.001"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))

    with pytest.raises(EngineError, match="unsafe"):
        engine.unpack(archive, tmp_path / "restored")
    assert not (tmp_path / "escape.txt").exists()


# --- names that are unsafe to write ------------------------------------------


def _member(name, kind=tarfile.REGTYPE):
    info = tarfile.TarInfo(name)
    info.type = kind
    return info


@pytest.mark.parametrize(
    "name",
    ["C:foo", "file.txt:stream", "NUL", "con.txt", "Aux", "COM1", "lpt9.log",
     "name.", "name ", 'a"b', "a<b", "a|b", "a?b", "a*b"],
)
def test_windows_unsafe_names_are_rejected_for_windows(name):
    with pytest.raises(EngineError, match="unsafe"):
        _check_member(_member(name), windows=True)


@pytest.mark.parametrize(
    "name",
    ["report.pdf", "ünï cödé.txt", "my file (1).txt", ".gitignore", "console.txt"],
)
def test_ordinary_names_are_accepted_everywhere(name):
    _check_member(_member(name), windows=True)
    _check_member(_member(name), windows=False)


def test_colons_are_fine_off_windows():
    _check_member(_member("a:b"), windows=False)  # legal on Linux and macOS


@pytest.mark.parametrize(
    "name", ["../x", "/abs", "dir/file", "a" + chr(92) + "b", "..", ".", ""]
)
def test_structural_tricks_are_rejected_on_every_platform(name):
    for windows in (True, False):
        with pytest.raises(EngineError):
            _check_member(_member(name), windows=windows)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE,
                                  tarfile.CHRTYPE, tarfile.FIFOTYPE])
def test_only_regular_files_are_accepted(kind):
    with pytest.raises(EngineError):
        _check_member(_member("name", kind), windows=False)


def test_extraction_refuses_to_leave_the_staging_folder(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()

    for name in ("../escape.txt", "sub/../../escape.txt", str(tmp_path / "x.txt")):
        with pytest.raises(EngineError, match="Unsafe"):
            NativeEngine._extract(_member(name), None, staging)
    assert not (tmp_path / "escape.txt").exists()
