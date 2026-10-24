"""Optional encryption for the native engine.

Needs the ``cryptography`` package (``pip install "batchvault[crypto]"``).

An encrypted archive is a header followed by one record per compressed block
and a final terminator record. Each record is AES-256-GCM encrypted with a
unique nonce; the header, the record number and a "last record" flag are
authenticated, so reordering, dropping or truncating records is detected. The
key comes from the password with scrypt and a random per-archive salt.
"""

from __future__ import annotations

import hashlib
import io
import os
import struct

from batchvault.errors import EngineError, EngineNotFoundError

MAGIC = b"BVENC1\x00\x00"
FORMAT_IDS = {"xz": 1, "gzip": 2, "bzip2": 3, "store": 4}
_ID_FORMATS = {value: key for key, value in FORMAT_IDS.items()}
SALT_SIZE = 16
PREFIX_SIZE = 4
HEADER_SIZE = len(MAGIC) + 2 + SALT_SIZE + PREFIX_SIZE

# scrypt cost: N = 2**log2_n (about 128 MiB of memory at the default), r = 8, p = 1.
# The value used is stored in the archive header so the default can rise later,
# and header values outside this range are refused: a damaged or hostile header
# must not be able to ask for terabytes of memory.
DEFAULT_LOG2_N = 17
KDF_LOG2_N_RANGE = (14, 18)
_SCRYPT_R = 8
_SCRYPT_P = 1

_LENGTH = struct.Struct(">I")
_INDEX = struct.Struct(">Q")
# A record holds one compressed block (16 MiB by default); anything near this
# size is damage, and the limit stops a bad length from allocating gigabytes.
_MAX_RECORD = 128 * 1024 * 1024

INSTALL_HINT = (
    "Encryption and signing need the optional 'cryptography' package: "
    'pip install "batchvault[crypto]"'
)


def require_cryptography() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError as exc:
        raise EngineNotFoundError(INSTALL_HINT) from exc


def make_header(fmt: str) -> bytes:
    return (
        MAGIC
        + bytes([FORMAT_IDS[fmt], DEFAULT_LOG2_N])
        + os.urandom(SALT_SIZE + PREFIX_SIZE)
    )


def is_encrypted_header(data: bytes) -> bool:
    return data.startswith(MAGIC)


def parse_header(data: bytes) -> tuple[str, int, bytes, bytes]:
    """Return ``(format, log2_n, salt, nonce_prefix)`` from an archive header."""
    if len(data) < HEADER_SIZE or not is_encrypted_header(data):
        raise EngineError("Not an encrypted BatchVault archive.")
    fmt = _ID_FORMATS.get(data[len(MAGIC)])
    if fmt is None:
        raise EngineError("Unknown format in encrypted archive header.")

    log2_n = data[len(MAGIC) + 1]
    low, high = KDF_LOG2_N_RANGE
    if not low <= log2_n <= high:
        raise EngineError("Unsupported key setting in the encrypted archive header.")

    start = len(MAGIC) + 2
    salt = data[start : start + SALT_SIZE]
    return fmt, log2_n, salt, data[start + SALT_SIZE : HEADER_SIZE]


class BlockCipher:
    """Encrypts and decrypts numbered records for one archive."""

    def __init__(self, password: str, header: bytes):
        require_cryptography()
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        _, log2_n, salt, prefix = parse_header(header)
        n = 1 << log2_n
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            maxmem=130 * n * _SCRYPT_R,  # scrypt needs about 128 * n * r bytes
            dklen=32,
        )
        self._aes = AESGCM(key)
        self._prefix = prefix
        self._header = header

    def _nonce(self, index: int) -> bytes:
        return self._prefix + _INDEX.pack(index)

    def _aad(self, index: int, last: bool) -> bytes:
        return self._header + _INDEX.pack(index) + (b"\x01" if last else b"\x00")

    def encrypt(self, index: int, data: bytes, last: bool = False) -> bytes:
        """Return a length-prefixed encrypted record."""
        sealed = self._aes.encrypt(self._nonce(index), data, self._aad(index, last))
        return _LENGTH.pack(len(sealed)) + sealed

    def decrypt(self, index: int, sealed: bytes) -> tuple[bytes, bool]:
        """Return ``(data, is_last)``; raise EngineError on a wrong password."""
        from cryptography.exceptions import InvalidTag

        for last in (False, True):
            try:
                data = self._aes.decrypt(
                    self._nonce(index), sealed, self._aad(index, last)
                )
            except InvalidTag:
                continue
            return data, last
        raise EngineError("Wrong password, or the encrypted archive is damaged.")


def _read_exact(source, count: int) -> bytes:
    chunks = []
    while count:
        chunk = source.read(count)
        if not chunk:
            break
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


class DecryptingReader(io.RawIOBase):
    """Read-only stream of the decrypted (still compressed) block data."""

    def __init__(self, source, cipher: BlockCipher):
        super().__init__()
        self._source = source
        self._cipher = cipher
        self._buffer = memoryview(b"")
        self._index = 0
        self._done = False

    def readable(self) -> bool:
        return True

    def _next_record(self) -> None:
        head = _read_exact(self._source, _LENGTH.size)
        if len(head) < _LENGTH.size:
            raise EngineError("Encrypted archive is truncated (no end marker).")

        (length,) = _LENGTH.unpack(head)
        if length > _MAX_RECORD:
            raise EngineError("Encrypted archive is damaged (bad record size).")

        sealed = _read_exact(self._source, length)
        if len(sealed) < length:
            raise EngineError("Encrypted archive is truncated.")

        data, self._done = self._cipher.decrypt(self._index, sealed)
        self._index += 1
        self._buffer = memoryview(data)

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        while not len(self._buffer):
            if self._done:
                return 0
            self._next_record()

        count = min(len(view), len(self._buffer))
        view[:count] = self._buffer[:count]
        self._buffer = self._buffer[count:]
        return count


def open_decrypted(source, password: str) -> tuple[str, DecryptingReader]:
    """Read the header from ``source``; return the inner format and a reader."""
    header = _read_exact(source, HEADER_SIZE)
    fmt = parse_header(header)[0]
    return fmt, DecryptingReader(source, BlockCipher(password, header))
