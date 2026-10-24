from __future__ import annotations

import io
import os
import re
import shutil
import time
import unicodedata
from pathlib import Path

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
    numbers = [split_volume_name(volume.name)[1] for volume in volumes]

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


class VolumeWriter:
    """Write-only file object that rolls over to a new volume every N bytes."""

    def __init__(self, directory: Path, base: str, volume_size: int):
        self._directory = directory
        self._base = base
        self._volume_size = volume_size
        self._file = None
        self._room = 0
        self.volumes: list[Path] = []

    def write(self, data) -> int:
        view = memoryview(data)
        total = len(view)

        while len(view):
            if self._room == 0:
                self._next_volume()
            chunk = view[: self._room]
            self._file.write(chunk)
            self._room -= len(chunk)
            view = view[len(chunk) :]

        return total

    def _next_volume(self) -> None:
        self._close_current()
        number = len(self.volumes) + 1
        path = self._directory / f"{self._base}.{number:03d}"
        self._file = path.open("wb")
        self.volumes.append(path)
        self._room = self._volume_size

    def _close_current(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def close(self) -> None:
        self._close_current()

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
