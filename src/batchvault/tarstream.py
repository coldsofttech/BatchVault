"""A tar stream for one file that can start at any byte offset.

``tarfile`` cannot begin writing in the middle of a file, but resuming inside a
large file needs exactly that. This produces the same bytes ``TarFile.add`` does
in stream mode (PAX header, the file's data, padding to a 512-byte block, the two
end-of-archive blocks and padding to the 10240-byte record), as a sequence of
chunks that can start at any offset. Archives are therefore unchanged, and a
test compares the two byte for byte.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterator
from pathlib import Path

CHUNK = 1024 * 1024


def normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Drop ownership so archives do not depend on the user who made them."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


class TarStream:
    """The tar bytes of one regular file, readable from any offset."""

    def __init__(self, source: Path, arcname: str):
        with tarfile.TarFile(
            fileobj=io.BytesIO(), mode="w", format=tarfile.PAX_FORMAT
        ) as tar:
            info = normalize(tar.gettarinfo(str(source), arcname=arcname))
            if not info.isreg():
                raise OSError(f"Not a regular file: {source}")
            self.header = info.tobuf(tar.format, tar.encoding, tar.errors)

        self.source = source
        self.size = info.size

        data_end = len(self.header) + self.size
        padding = -self.size % tarfile.BLOCKSIZE
        end = data_end + padding + 2 * tarfile.BLOCKSIZE
        self._tail = padding + 2 * tarfile.BLOCKSIZE + (-end % tarfile.RECORDSIZE)
        self.length = data_end + self._tail

    def chunks(self, start: int = 0) -> Iterator[bytes]:
        """The stream from byte ``start`` to the end."""
        if not 0 <= start <= self.length:
            raise ValueError(f"start {start} is outside the stream (0-{self.length})")

        position = start
        header_size = len(self.header)
        if position < header_size:
            yield self.header[position:]
            position = header_size

        data_end = header_size + self.size
        if position < data_end:
            with self.source.open("rb") as handle:
                handle.seek(position - header_size)
                left = data_end - position
                while left:
                    data = handle.read(min(CHUNK, left))
                    if not data:  # the file shrank after its size was read
                        raise OSError("unexpected end of data")
                    left -= len(data)
                    yield data
            position = data_end

        remaining = self.length - position
        while remaining:
            count = min(CHUNK, remaining)
            yield bytes(count)
            remaining -= count
