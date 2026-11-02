from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from batchvault.errors import VolumeError
from batchvault.security import restrict_permissions

VOLUME_RE = re.compile(r"^(?P<base>.+\.(?:7z|bv))\.(?P<number>\d{3,})$", re.IGNORECASE)


def split_volume_name(name: str) -> tuple[str, int] | None:
    """Return ``(base, number)`` for ``intro.mp4.7z.003``, or None if not a volume."""
    match = VOLUME_RE.match(name)
    if not match:
        return None
    return match.group("base"), int(match.group("number"))


def _same_name(a: str, b: str) -> bool:
    """Compare names ignoring case and Unicode form (macOS decomposes accents)."""
    return (
        unicodedata.normalize("NFC", a).casefold()
        == unicodedata.normalize("NFC", b).casefold()
    )


def existing_volumes(directory: Path, base: str) -> list[Path]:
    """Volumes of ``base`` already present in ``directory``, in volume order."""
    if not directory.is_dir():
        return []

    found = []
    for path in directory.iterdir():
        parsed = split_volume_name(path.name)
        if path.is_file() and parsed and _same_name(parsed[0], base):
            found.append((parsed[1], path))
    return [path for _, path in sorted(found)]


def find_volumes(path: Path) -> list[Path]:
    """Find every volume of the set that ``path`` belongs to.

    Any volume of the set may be given. Raises VolumeError if the path is not a
    volume or if a volume inside the numbered range is missing. A missing
    *trailing* volume cannot be detected without a manifest.
    """
    parsed = split_volume_name(path.name)
    if parsed is None:
        raise VolumeError(f"Not an archive volume: {path}")

    volumes = existing_volumes(path.parent, parsed[0])
    numbers = []
    for volume in volumes:
        info = split_volume_name(volume.name)
        if info:
            numbers.append(info[1])

    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    if missing:
        listed = ", ".join(f"{number:03d}" for number in missing)
        raise VolumeError(
            f"Missing volume(s) {listed} for {parsed[0]} in {path.parent}"
        )

    return volumes


STAGING_PREFIX = ".batchvault-"


def cleanup_staging(root: Path, max_age: float = 86400.0) -> int:
    """Remove staging folders left behind by a killed run; return how many.

    Looks through the whole tree under ``root``. Only folders untouched for
    ``max_age`` seconds are removed, so a run in progress is never disturbed.
    """
    if not root.is_dir():
        return 0

    removed = 0
    now = time.time()
    for current, dirnames, _ in os.walk(root):  # does not follow links
        for name in list(dirnames):
            if not name.startswith(STAGING_PREFIX):
                continue
            dirnames.remove(name)  # never look inside a staging folder
            item = Path(current) / name
            try:
                if not item.is_symlink() and now - item.stat().st_mtime > max_age:
                    shutil.rmtree(item)
                    removed += 1
            except OSError:
                continue
    return removed


def install_volumes(built: list[Path], dest_dir: Path, existing: list[Path]):
    """Move a finished volume set into ``dest_dir``, replacing ``existing``.

    Old volumes are removed only here, after the new set was built completely.
    """
    for old in existing:
        old.unlink()

    installed = []
    for volume in built:
        # Archives may hold private data; do not let umask make them readable by
        # other users (no effect on Windows, where folder permissions apply).
        restrict_permissions(volume)
        target = dest_dir / volume.name
        os.replace(volume, target)
        installed.append(target)
    return installed


@dataclass
class Adopted:
    """Volumes already on disk that a resumed ``VolumeWriter`` carries on from."""

    complete: list[Path]  # full volumes, in order
    partial: Path | None = None  # the volume that was being written, if any
    partial_hash: Any = None  # SHA-256 state of its first ``partial_size`` bytes
    partial_size: int = 0


class VolumeWriter:
    """Write-only file object that rolls over to a new volume every N bytes.

    Each volume is hashed as it is written. When one is full it is flushed to disk
    and ``on_volume(index, size, sha256)`` is called, so a resume can trust it.
    ``position`` is the total number of bytes written, and ``sync_state`` makes
    the volume being written durable and reports how far it got.
    """

    def __init__(
        self,
        directory: Path,
        base: str,
        volume_size: int,
        *,
        on_volume: Callable[[int, int, str], None] | None = None,
        adopt: Adopted | None = None,
    ):
        self._directory = directory
        self._base = base
        self._volume_size = volume_size
        self._on_volume = on_volume
        self._file: BinaryIO | None = None
        self._room = 0
        self._hash = hashlib.sha256()
        self.position = 0
        self.volumes: list[Path] = []

        if adopt is not None:
            self.volumes = list(adopt.complete)
            self.position = len(adopt.complete) * volume_size
            if adopt.partial is not None:
                self._file = adopt.partial.open("r+b")
                self._file.seek(adopt.partial_size)
                self._room = volume_size - adopt.partial_size
                self._hash = adopt.partial_hash
                self.volumes.append(adopt.partial)
                self.position += adopt.partial_size

    def write(self, data) -> int:
        view = memoryview(data)
        total = len(view)

        while len(view):
            if self._file is None:
                self._next_volume()
            assert self._file is not None  # opened by _next_volume
            chunk = view[: self._room]
            self._file.write(chunk)
            self._hash.update(chunk)
            self._room -= len(chunk)
            self.position += len(chunk)
            view = view[len(chunk) :]
            if self._room == 0:
                self._finish_volume()

        return total

    def _next_volume(self) -> None:
        number = len(self.volumes) + 1
        path = self._directory / f"{self._base}.{number:03d}"
        self._file = path.open("wb")
        self.volumes.append(path)
        self._room = self._volume_size
        self._hash = hashlib.sha256()

    def _finish_volume(self) -> None:
        """A volume is full: make it durable, close it and report its checksum."""
        assert self._file is not None
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()
        self._file = None
        if self._on_volume is not None:
            self._on_volume(
                len(self.volumes), self._volume_size, self._hash.hexdigest()
            )

    def sync_state(self) -> tuple[int, str]:
        """Flush the open volume to disk; return ``(position, prefix_sha256)``.

        The prefix hash covers the bytes already written to the volume being
        filled (empty when no volume is open, i.e. we are on a volume boundary).
        """
        if self._file is None:
            return self.position, ""
        self._file.flush()
        os.fsync(self._file.fileno())
        return self.position, self._hash.hexdigest()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class VolumeReader(io.RawIOBase):
    """Read-only file object that reads a volume set as one continuous stream."""

    def __init__(self, paths):
        super().__init__()
        self._paths = list(paths)
        self._index = 0
        self._file = None

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        view = memoryview(buffer).cast("B")
        if not len(view):
            return 0

        while True:
            if self._file is None:
                if self._index >= len(self._paths):
                    return 0
                self._file = self._paths[self._index].open("rb")
                self._index += 1

            count = self._file.readinto(view)
            if count:
                return count

            self._file.close()
            self._file = None

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
        super().close()
