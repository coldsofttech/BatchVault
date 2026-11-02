"""Block-parallel compression for the native engine.

The tar stream is cut into independent blocks. Each block is compressed as its
own xz, gzip or bzip2 member on a thread pool and the members are written in
order. Concatenated members are a valid single stream for all three formats, so
the joined volumes can be read by the standard tools too.
"""

from __future__ import annotations

import bz2
import gzip
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from batchvault import crypto
from batchvault.errors import EngineError, EngineNotFoundError
from batchvault.options import FORMATS, MIB

try:
    import lzma
except ImportError:  # Python built without liblzma
    lzma = None  # type: ignore[assignment]

BLOCK_SIZE = 16 * MIB

_XZ_MAGIC = b"\xfd7zXZ\x00"
_GZIP_MAGIC = b"\x1f\x8b"
_BZIP2_MAGIC = b"BZh"
_TAR_MAGIC_OFFSET = 257


def check_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise EngineError(f"Unknown format: {fmt}")
    if fmt == "xz" and lzma is None:
        raise EngineNotFoundError(
            "This Python was built without lzma support, so the xz format "
            "is unavailable. Use --format gzip or bzip2."
        )


def compress_block(fmt: str, level: int, data: bytes) -> bytes:
    """Compress one block as a complete, independent member."""
    if fmt == "xz":
        return lzma.compress(data, format=lzma.FORMAT_XZ, preset=level)
    if fmt == "gzip":
        return gzip.compress(data, compresslevel=level, mtime=0)
    if fmt == "bzip2":
        # bzip2 has no level 0; the lowest level is the closest match.
        return bz2.compress(data, compresslevel=max(level, 1))
    return data


def open_decompressed(fmt: str, fileobj):
    """Wrap a file object so reading it yields the uncompressed tar stream."""
    check_format(fmt)
    if fmt == "xz":
        return lzma.LZMAFile(fileobj, "rb")
    if fmt == "gzip":
        return gzip.GzipFile(fileobj=fileobj, mode="rb")
    if fmt == "bzip2":
        return bz2.BZ2File(fileobj, "rb")
    return fileobj


def sniff_archive(path) -> tuple[str, bool]:
    """Identify ``(format, encrypted)`` from the first bytes of a volume."""
    with open(path, "rb") as handle:
        head = handle.read(max(_TAR_MAGIC_OFFSET + 5, crypto.HEADER_SIZE))

    if crypto.is_encrypted_header(head):
        return crypto.parse_header(head)[0], True
    if head.startswith(_XZ_MAGIC):
        return "xz", False
    if head.startswith(_GZIP_MAGIC):
        return "gzip", False
    if head.startswith(_BZIP2_MAGIC):
        return "bzip2", False
    if head[_TAR_MAGIC_OFFSET : _TAR_MAGIC_OFFSET + 5] == b"ustar":
        return "store", False
    raise EngineError(f"Unrecognised archive format: {path}")


def sniff_format(path) -> str:
    """Identify the compression format from the first bytes of a volume."""
    return sniff_archive(path)[0]


def _process(fmt: str, level: int, block: bytes, cipher, index: int) -> bytes:
    data = compress_block(fmt, level, block)
    return cipher.encrypt(index, data) if cipher else data


class BlockWriter:
    """Write-only file object that compresses fixed-size blocks in parallel.

    At most ``2 * jobs`` blocks are in flight, which bounds memory use.
    """

    def __init__(
        self,
        sink,
        fmt: str,
        level: int,
        block_size: int,
        jobs: int,
        cipher=None,
        header: bytes = b"",
        *,
        start_block: int = 0,
        write_header: bool = True,
        on_block: Callable[[int], None] | None = None,
    ):
        self._sink = sink
        self._fmt = fmt
        self._level = level
        self._block_size = block_size
        self._cipher = cipher
        self._max_pending = jobs * 2
        # With no jobs, blocks are compressed in the calling thread.
        self._executor = ThreadPoolExecutor(max_workers=jobs) if jobs else None
        self._pending: deque[Future[bytes]] = deque()
        self._buffer = bytearray()
        # Blocks are numbered from ``start_block`` when a run is resumed; the
        # nonce of each encrypted record is its number, so numbering must continue.
        self._count = start_block
        self._written = start_block
        self._on_block = on_block
        self._final: int | None = None  # the last, partial block: no checkpoint
        if cipher and write_header:
            sink.write(header)

    def write(self, data) -> int:
        self._buffer += data
        while len(self._buffer) >= self._block_size:
            block = bytes(self._buffer[: self._block_size])
            del self._buffer[: self._block_size]
            self._submit(block)
        return len(data)

    def _submit(self, block: bytes) -> None:
        if self._executor is None:
            self._sink.write(
                _process(self._fmt, self._level, block, self._cipher, self._count)
            )
            self._count += 1
            self._block_written()
            return
        self._pending.append(
            self._executor.submit(
                _process, self._fmt, self._level, block, self._cipher, self._count
            )
        )
        self._count += 1
        while len(self._pending) > self._max_pending:
            self._write_oldest()

    def _write_oldest(self) -> None:
        self._sink.write(self._pending.popleft().result())
        self._block_written()

    def _block_written(self) -> None:
        """A block reached the sink, in order: report how many are done."""
        index = self._written
        self._written += 1
        if self._on_block is not None and index != self._final:
            self._on_block(self._written)

    def close(self) -> None:
        try:
            if self._buffer:
                # A checkpoint must sit on a full-block boundary, so the last
                # partial block never gets one.
                self._final = self._count
                self._submit(bytes(self._buffer))
                self._buffer.clear()
            while self._pending:
                self._write_oldest()
            if self._cipher:
                # The end marker lets a reader detect a truncated archive.
                self._sink.write(self._cipher.encrypt(self._count, b"", last=True))
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *exc_info):
        if exc_type is None:
            self.close()
        else:
            self._shutdown()
