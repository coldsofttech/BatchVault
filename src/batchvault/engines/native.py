from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from batchvault import crypto
from batchvault.blocks import (
    BLOCK_SIZE,
    BlockWriter,
    check_format,
    open_decompressed,
    sniff_archive,
)
from batchvault.checkpoint import Spec
from batchvault.engines.base import Capabilities, Engine
from batchvault.errors import EngineError, OutputExistsError, ResumeError
from batchvault.names import unsafe_on_windows as _unsafe_on_windows
from batchvault.options import MIB, PackOptions
from batchvault.tarstream import TarStream
from batchvault.volumes import (
    STAGING_PREFIX,
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
_READ_ERRORS: tuple[type[BaseException], ...] = (
    tarfile.TarError,
    EOFError,
    zlib.error,
    OSError,
    *_LZMA_ERRORS,
)

_STREAM_BUFFER = MIB
_CHUNK = MIB
# Data allowed after the tar end marker; a real archive has only padding.
_MAX_TRAILING = 4 * MIB


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


def _check_resume_password(volumes: list[Path], password: str) -> None:
    """Refuse to continue a file with a password other than the one it began with.

    New blocks would be sealed with a different key under the same header and
    the archive could never be restored, so the first record already on disk is
    opened with the password first.
    """
    with VolumeReader(volumes) as reader:
        try:
            _, records = crypto.open_decrypted(reader, password)
            records.read(1)
        except EngineError as exc:
            raise ResumeError(
                "The password does not match the data already written for this "
                "file. Use the password of the earlier run."
            ) from exc


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

        stat = source.stat()
        stream = TarStream(source, source.name)
        dest_dir.mkdir(parents=True, exist_ok=True)

        spec = Spec(
            fmt=options.format,
            level=options.level,
            block_size=self._block_size,
            volume_size=options.volume_size,
            encrypted=bool(options.password),
            size=stream.size,
            mtime_ns=stat.st_mtime_ns,
        )

        # Build in a staging folder so a failed run never damages what exists.
        # A resumable build (one reporting checkpoints) keeps its folder when it
        # fails, at a stable name, so the next attempt can carry on from it.
        checkpoints = options.checkpoints
        if checkpoints is not None:
            staging = dest_dir / checkpoints.staging_name
            staging.mkdir(mode=0o700, exist_ok=True)
        else:
            staging = Path(tempfile.mkdtemp(dir=dest_dir, prefix=STAGING_PREFIX))

        try:
            installed = self._build(
                stream, staging, base, dest_dir, existing, options, spec, checkpoints
            )
        except BaseException:
            if checkpoints is None:
                shutil.rmtree(staging, ignore_errors=True)
            raise
        shutil.rmtree(staging, ignore_errors=True)
        return installed

    def _build(
        self,
        stream: TarStream,
        staging: Path,
        base: str,
        dest_dir: Path,
        existing: list[Path],
        options: PackOptions,
        spec: Spec,
        checkpoints: Any,
    ) -> list[Path]:
        resume = checkpoints.find_resume(staging, base, spec) if checkpoints else None

        header = cipher = None
        if options.password:
            header = resume.header if resume else crypto.make_header(options.format)
            cipher = crypto.BlockCipher(options.password, header)
            if resume:
                _check_resume_password(resume.volumes, options.password)

        on_volume = checkpoints.volume_done if checkpoints else None
        with VolumeWriter(
            staging,
            base,
            options.volume_size,
            on_volume=on_volume,
            adopt=resume.adopted if resume else None,
        ) as volumes:

            def on_block(done: int) -> None:
                position, prefix = volumes.sync_state()
                checkpoints.checkpoint(spec, done, position, prefix, header or b"")

            with BlockWriter(
                volumes,
                options.format,
                options.level,
                self._block_size,
                options.jobs,
                cipher=cipher,
                header=header or b"",
                start_block=resume.blocks if resume else 0,
                write_header=resume is None,
                on_block=on_block if checkpoints else None,
            ) as blocks:
                # Blocks are cut at exact multiples of the block size, so the
                # blocks already written say exactly where in the tar stream to
                # carry on.
                start = resume.blocks * self._block_size if resume else 0
                if resume:
                    done = min(max(start - len(stream.header), 0), stream.size)
                    checkpoints.mark_continued(len(resume.volumes), done)
                for chunk in stream.chunks(start):
                    blocks.write(chunk)

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
                source: Any = reader
                if encrypted:
                    assert password is not None  # checked above
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
