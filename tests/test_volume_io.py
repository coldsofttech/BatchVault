import os

from batchvault.blocks import BlockWriter, compress_block
from batchvault.volumes import VolumeReader, VolumeWriter


def test_writer_rolls_over_and_reader_joins(tmp_path):
    data = os.urandom(10_500)

    with VolumeWriter(tmp_path, "x.bv", 4000) as writer:
        writer.write(data[:5000])
        writer.write(data[5000:])

    assert [v.name for v in writer.volumes] == ["x.bv.001", "x.bv.002", "x.bv.003"]
    assert [v.stat().st_size for v in writer.volumes] == [4000, 4000, 2500]

    with VolumeReader(writer.volumes) as reader:
        assert reader.read() == data


def test_writer_with_no_data_creates_no_volumes(tmp_path):
    with VolumeWriter(tmp_path, "x.bv", 100) as writer:
        pass
    assert writer.volumes == []


def test_block_writer_keeps_order_across_threads(tmp_path):
    blocks = [bytes([i]) * 1000 for i in range(20)]

    with VolumeWriter(tmp_path, "x.bv", 10_000_000) as sink:
        with BlockWriter(sink, "store", 5, 1000, jobs=4) as writer:
            for block in blocks:
                writer.write(block)

    assert sink.volumes[0].read_bytes() == b"".join(blocks)


def test_block_writer_flushes_partial_last_block(tmp_path):
    with VolumeWriter(tmp_path, "x.bv", 10_000_000) as sink:
        with BlockWriter(sink, "gzip", 5, 1000, jobs=2) as writer:
            writer.write(b"a" * 1500)

    expected = compress_block("gzip", 5, b"a" * 1000) + compress_block(
        "gzip", 5, b"a" * 500
    )
    assert sink.volumes[0].read_bytes() == expected
