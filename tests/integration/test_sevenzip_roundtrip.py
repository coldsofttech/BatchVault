import os
import unicodedata

import pytest

from batchvault.engines.sevenzip import SevenZipEngine
from batchvault.errors import EngineError, OutputExistsError
from batchvault.options import PackOptions

pytestmark = pytest.mark.integration

engine = SevenZipEngine()

if not engine.is_available():
    pytest.skip("7-Zip is not installed", allow_module_level=True)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "données 1.bin"
    path.write_bytes(os.urandom(20_000))
    return path


def test_roundtrip_with_split_volumes(tmp_path, source):
    archives = tmp_path / "archives"
    volumes = engine.pack(source, archives, PackOptions(volume_size=4096, jobs=2))

    assert len(volumes) > 1
    # macOS may report accents in decomposed form; the name is the same.
    assert unicodedata.normalize("NFC", volumes[0].name) == "données 1.bin.7z.001"

    engine.test(volumes[0])

    restored = engine.unpack(volumes[2], tmp_path / "restored")
    assert [unicodedata.normalize("NFC", p.name) for p in restored] == [
        unicodedata.normalize("NFC", source.name)
    ]
    assert restored[0].read_bytes() == source.read_bytes()


def test_existing_archive_is_not_replaced_without_force(tmp_path, source):
    archives = tmp_path / "archives"
    engine.pack(source, archives, PackOptions())

    with pytest.raises(OutputExistsError):
        engine.pack(source, archives, PackOptions())

    engine.pack(source, archives, PackOptions(force=True))


def test_unpack_does_not_overwrite_without_force(tmp_path, source):
    volumes = engine.pack(source, tmp_path / "archives", PackOptions())
    output = tmp_path / "restored"
    engine.unpack(volumes[0], output)

    with pytest.raises(OutputExistsError):
        engine.unpack(volumes[0], output)

    engine.unpack(volumes[0], output, force=True)


def test_corrupt_volume_fails_test(tmp_path, source):
    volumes = engine.pack(source, tmp_path / "archives", PackOptions())
    data = bytearray(volumes[0].read_bytes())
    data[len(data) // 2] ^= 0xFF
    volumes[0].write_bytes(bytes(data))

    with pytest.raises(EngineError):
        engine.test(volumes[0])
