import os

import pytest

pytest.importorskip("cryptography")

from batchvault.engines.native import NativeEngine  # noqa: E402
from batchvault.errors import EngineError  # noqa: E402
from batchvault.options import PackOptions  # noqa: E402

engine = NativeEngine(block_size=4096)
PASSWORD = "correct horse"


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "secret données.bin"
    path.write_bytes(os.urandom(20_000))
    return path


def pack(source, tmp_path, **kwargs):
    options = PackOptions(volume_size=3000, password=PASSWORD, **kwargs)
    return engine.pack(source, tmp_path / "archives", options)


@pytest.mark.parametrize("fmt", ["xz", "gzip", "bzip2", "store"])
def test_encrypted_roundtrip(tmp_path, source, fmt):
    volumes = pack(source, tmp_path, format=fmt, jobs=3)
    assert len(volumes) > 1

    engine.test(volumes[0], password=PASSWORD)
    restored = engine.unpack(volumes[0], tmp_path / "out", password=PASSWORD)
    assert restored[0].read_bytes() == source.read_bytes()


def test_plaintext_does_not_appear_in_volumes(tmp_path):
    source = tmp_path / "marker.txt"
    source.write_text("TOP-SECRET-MARKER " * 500)
    volumes = pack(source, tmp_path, format="store")
    assert b"TOP-SECRET-MARKER" not in b"".join(v.read_bytes() for v in volumes)


def test_wrong_password(tmp_path, source):
    volumes = pack(source, tmp_path)
    with pytest.raises(EngineError, match="Wrong password"):
        engine.test(volumes[0], password="nope")


def test_missing_password(tmp_path, source):
    volumes = pack(source, tmp_path)
    with pytest.raises(EngineError, match="encrypted"):
        engine.test(volumes[0])


def test_flipped_byte_is_detected(tmp_path, source):
    volumes = pack(source, tmp_path)
    data = bytearray(volumes[1].read_bytes())
    data[len(data) // 2] ^= 0xFF
    volumes[1].write_bytes(bytes(data))

    with pytest.raises(EngineError):
        engine.test(volumes[0], password=PASSWORD)


def test_truncation_is_detected(tmp_path, source):
    volumes = pack(source, tmp_path)
    last = volumes[-1]
    last.write_bytes(last.read_bytes()[:-1])

    with pytest.raises(EngineError):
        engine.test(volumes[0], password=PASSWORD)


def test_dropping_whole_records_is_detected(tmp_path, source):
    # Removing the end marker (the last record) must not look like a valid archive.
    volumes = pack(source, tmp_path, format="store")
    joined = b"".join(v.read_bytes() for v in volumes)
    marker = 4 + 16  # length prefix + GCM tag of the empty end record
    for volume in volumes:
        volume.unlink()
    volumes[0].write_bytes(joined[:-marker])

    with pytest.raises(EngineError):
        engine.test(volumes[0], password=PASSWORD)


# --- key derivation cost and header limits -----------------------------------


def test_real_default_is_a_strong_setting(monkeypatch):
    from batchvault import crypto

    monkeypatch.undo()  # drop the cheap setting the test suite uses
    assert crypto.DEFAULT_LOG2_N >= 17  # at least 128 MiB of scrypt memory
    assert crypto.parse_header(crypto.make_header("xz"))[1] == crypto.DEFAULT_LOG2_N


def test_archives_open_whatever_cost_they_were_made_with(tmp_path, source, monkeypatch):
    from batchvault import crypto

    monkeypatch.setattr(crypto, "DEFAULT_LOG2_N", 14)
    volumes = pack(source, tmp_path)

    monkeypatch.setattr(crypto, "DEFAULT_LOG2_N", 16)  # the default moved on
    restored = engine.unpack(volumes[0], tmp_path / "out", password=PASSWORD)
    assert restored[0].read_bytes() == source.read_bytes()


@pytest.mark.parametrize("log2_n", [0, 13, 19, 30, 255])
def test_header_asking_for_absurd_memory_is_refused(tmp_path, source, log2_n):
    from batchvault import crypto

    volumes = pack(source, tmp_path)
    data = bytearray(volumes[0].read_bytes())
    data[len(crypto.MAGIC) + 1] = log2_n  # a hostile or damaged header
    volumes[0].write_bytes(bytes(data))

    with pytest.raises(EngineError, match="key setting"):
        engine.test(volumes[0], password=PASSWORD)


def test_header_cost_cannot_be_lowered_without_detection(tmp_path, source):
    from batchvault import crypto

    volumes = pack(source, tmp_path)
    data = bytearray(volumes[0].read_bytes())
    data[len(crypto.MAGIC) + 1] = crypto.DEFAULT_LOG2_N + 1  # in range, but changed
    volumes[0].write_bytes(bytes(data))

    with pytest.raises(EngineError):  # a different key, and the header is signed
        engine.test(volumes[0], password=PASSWORD)


def test_an_absurd_record_length_is_refused_without_allocating(tmp_path):
    import io

    from batchvault import crypto

    header = crypto.make_header("store")
    stream = io.BytesIO(header + (0xFFFFFFFF).to_bytes(4, "big") + b"x" * 64)
    _, reader = crypto.open_decrypted(stream, PASSWORD)

    with pytest.raises(EngineError, match="bad record size"):
        reader.read(10)
