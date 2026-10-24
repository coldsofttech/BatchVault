from __future__ import annotations

import os
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath

from batchvault import crypto
from batchvault.blocks import (
    BLOCK_SIZE,
    BlockWriter,
    check_format,
    open_decompressed,
    sniff_archive,
)
from batchvault.engines.base import Capabilities, Engine
from batchvault.errors import EngineError, OutputExistsError
from batchvault.names import unsafe_on_windows as _unsafe_on_windows
from batchvault.options import MIB, PackOptions
from batchvault.volumes import (
    VolumeReader,
    VolumeWriter,
    existing_volumes,
    find_volumes,
    install_volumes,
)

try:
    import lzma

    _LZMA_ERRORS: tuple[type[Exception], ...] = (lzma.LZMAError,)
except ImportError:
    _LZMA_ERRORS = ()

# Errors raised while reading damaged, truncated or unreadable archive data.
_READ_ERRORS = (tarfile.TarError, EOFError, zlib.error, OSError, *_LZMA_ERRORS)

_STREAM_BUFFER = MIB
_CHUNK = MIB
# Data allowed after the tar end marker; a real archive has only padding.
_MAX_TRAILING = 4 * MIB


def _normalize(info: tarfile.TarInfo) -> tarfile.TarInfo:
    """Drop ownership so archives do not depend on the user who made them."""
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    return info


class _Budget:
    """Caps the total size of the files restored from one archive."""

    def __init__(self, limit: int | None):
        self.remaining = limit

    def charge(self, member: tarfile.TarInfo) -> None:
        if self.remaining is None:
            return
        if member.size > self.remaining:
            raise EngineError(
                f"{member.name!r} would expand beyond the allowed size; "
                "the archive was not restored."
            )
        self.remaining -= member.size


def _check_member(member: tarfile.TarInfo, windows: bool | None = None) -> None:
    """Accept only a plain, single-part regular file name that is safe to write."""
    if windows is None:
        windows = os.name == "nt"

    path = PurePosixPath(member.name)
    if (
        not member.isfile()
        or path.is_absolute()
        or len(path.parts) != 1
        or path.name in ("", ".", "..")
        or "\\" in member.name
        or (windows and _unsafe_on_windows(member.name))
    ):
        raise EngineError(f"Unsupported or unsafe entry in archive: {member.name!r}")


class NativeEngine(Engine):
    """Standard-library engine: tar stream, block-parallel compression."""

    name = "native"
    capabilities = Capabilities(
        levels=range(0, 10), threads_per_file=True, archive_suffix=".bv"
    )

    def __init__(self, block_size: int = BLOCK_SIZE):
        self._block_size = block_size

    def is_available(self) -> bool:
        return True

    def pack(self, source: Path, dest_dir: Path, options: PackOptions) -> list[Path]:
        if not source.is_file():
            raise FileNotFoundError(f"Source file not found: {source}")
        check_format(options.format)

        base = f"{source.name}{self.capabilities.archive_suffix}"
        existing = existing_volumes(dest_dir, base)
        if existing and not options.force:
            raise OutputExistsError(
                f"Archive volumes already exist in {dest_dir}. Use --force to replace."
            )

        header = cipher = None
        if options.password:
            header = crypto.make_header(options.format)
            cipher = crypto.BlockCipher(options.password, header)

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Build in a temporary folder so a failed run never damages what exists.
        with tempfile.TemporaryDirectory(dir=dest_dir, prefix=".batchvault-") as tmp:
            with VolumeWriter(Path(tmp), base, options.volume_size) as volumes:
                with BlockWriter(
                    volumes,
                    options.format,
                    options.level,
                    self._block_size,
                    options.jobs,
                    cipher=cipher,
                    header=header or b"",
                ) as blocks:
                    with tarfile.open(
                        fileobj=blocks,
                        mode="w|",
                        format=tarfile.PAX_FORMAT,
                        bufsize=_STREAM_BUFFER,
                    ) as tar:
                        tar.add(source, arcname=source.name, filter=_normalize)

            return install_volumes(volumes.volumes, dest_dir, existing)

    def unpack(
        self,
        volume: Path,
        output_dir: Path,
        *,
        force: bool = False,
        password: str | None = None,
        max_bytes: int | None = None,
    ) -> list[Path]:
        volumes = find_volumes(volume)
        output_dir.mkdir(parents=True, exist_ok=True)
        budget = _Budget(max_bytes)

        def handle(member, tar) -> None:
            budget.charge(member)  # refuse a decompression bomb before writing
            self._extract(member, tar, staging)

        with tempfile.TemporaryDirectory(dir=output_dir, prefix=".batchvault-") as tmp:
            staging = Path(tmp)
            self._read(volumes, handle, password)

            extracted = sorted(staging.iterdir())
            if not extracted:
                raise EngineError(f"No files found in archive {volumes[0]}")

            conflicts = [
                output_dir / item.name
                for item in extracted
                if (output_dir / item.name).exists()
            ]
            if conflicts and not force:
                names = ", ".join(path.name for path in conflicts)
                raise OutputExistsError(
                    f"Destination already exists: {names}. Use --force to overwrite."
                )

            restored = []
            for item in extracted:
                target = output_dir / item.name
                os.replace(item, target)
                restored.append(target)

        return restored

    def test(self, volume: Path, *, password: str | None = None) -> None:
        self._read(find_volumes(volume), self._drain_member, password)

    @staticmethod
    def _extract(member, tar, staging: Path) -> None:
        # Belt and braces next to _check_member and the "data" filter: whatever
        # the name looks like, the file must end up inside the staging folder.
        root = staging.resolve()
        if root not in (root / member.name).resolve().parents:
            raise EngineError(f"Unsafe entry in archive: {member.name!r}")
        tar.extract(member, staging, filter="data")

    @staticmethod
    def _drain_member(member, tar) -> None:
        handle = tar.extractfile(member)
        while handle.read(_CHUNK):
            pass

    @staticmethod
    def _read(volumes: list[Path], handle_member, password: str | None) -> None:
        """Stream the archive and call ``handle_member(member, tar)`` for each file."""
        fmt, encrypted = sniff_archive(volumes[0])
        if encrypted and not password:
            raise EngineError(
                f"{volumes[0].name} is encrypted. A password is required."
            )

        try:
            with VolumeReader(volumes) as reader:
                source = reader
                if encrypted:
                    fmt, source = crypto.open_decrypted(reader, password)

                with open_decompressed(fmt, source) as stream:
                    with tarfile.open(fileobj=stream, mode="r|") as tar:
                        for member in tar:
                            _check_member(member)
                            handle_member(member, tar)

                    # Read to the very end so a truncated stream is detected. A
                    # real archive has almost nothing after the tar end marker.
                    trailing = 0
                    while chunk := stream.read(_CHUNK):
                        trailing += len(chunk)
                        if trailing > _MAX_TRAILING:
                            raise EngineError(
                                f"{volumes[0].name} has unexpected data after the "
                                "end of the archive."
                            )
        except _READ_ERRORS as exc:
            raise EngineError(
                f"Archive is corrupt or incomplete ({volumes[0].name}): {exc}"
            ) from exc
