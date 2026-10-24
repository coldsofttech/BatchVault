from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Status(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    PARTIAL = "partial"


@dataclass
class FileResult:
    """Outcome of one file in a pack, unpack or verify run.

    For ``pack`` the ``source`` is the original file and ``archive`` its first
    volume. For ``unpack`` and ``verify`` the ``source`` is the archive (its first
    volume) and, for ``unpack``, ``restored`` lists the files written.
    """

    source: Path
    status: Status
    message: str = ""
    size: int = 0
    volumes: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    archive: Path | None = None  # first volume of the archive (pack)
    stored_size: int = 0  # total size of the volumes (pack)
    restored: list[Path] = field(default_factory=list)  # files written (unpack)
    folder: str = ""  # folder of the file under the root ("" = flat)
    name: str = ""  # the original file's name (pack, unpack and verify)
    level: int | None = None  # compression level used (pack)

    def to_dict(self) -> dict:
        return {
            "source": str(self.source),
            "name": self.name,
            "level": self.level,
            "folder": self.folder,
            "status": self.status.value,
            "message": self.message,
            "size": self.size,
            "volumes": [str(volume) for volume in self.volumes],
            "archive": str(self.archive) if self.archive else None,
            "stored_size": self.stored_size,
            "restored": [str(path) for path in self.restored],
            "warnings": self.warnings,
        }


@dataclass
class RunResult:
    """Structured outcome of a whole run."""

    entries: list[FileResult] = field(default_factory=list)
    manifest: Path | None = None
    warnings: list[str] = field(default_factory=list)
    engine: str = ""

    @property
    def status(self) -> Status:
        statuses = {entry.status for entry in self.entries}
        if not statuses or statuses == {Status.SUCCESS}:
            return Status.SUCCESS
        if statuses == {Status.SKIPPED}:
            return Status.SKIPPED
        if Status.SUCCESS in statuses:
            return Status.PARTIAL
        return Status.FAILED

    def count(self, status: Status) -> int:
        return sum(1 for entry in self.entries if entry.status == status)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "engine": self.engine,
            "manifest": str(self.manifest) if self.manifest else None,
            "warnings": self.warnings,
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class FileEntry:
    """A file found by ``list_files`` or an archive found by ``list_archives``."""

    path: Path
    size: int | None = None
    root: Path | None = None  # folder a file was listed from (``list files``)

    def to_dict(self) -> dict:
        data = {"path": str(self.path), "size": self.size}
        if self.root is not None:
            # Lets ``pack`` keep the listed folder's own name in the archives.
            data["root"] = str(self.root)
        return data


@dataclass(frozen=True)
class ProgressEvent:
    """Progress report. Callbacks run on worker threads and must be thread-safe.

    ``kind`` is ``"plan"`` (once, before work: ``size`` is the total bytes if
    known and ``count`` the number of files), then ``"start"`` and ``"finish"``
    for each file.
    """

    kind: str
    source: Path
    size: int = 0
    status: Status | None = None
    count: int = 0

    def to_dict(self) -> dict:
        return {
            "event": self.kind,
            "source": str(self.source),
            "size": self.size,
            "status": self.status.value if self.status else None,
            "count": self.count,
        }


@dataclass(frozen=True)
class InspectEntry:
    """What the manifest (or, without one, the file names) say about an archive."""

    name: str
    path: Path  # first volume
    engine: str
    format: str | None = None
    level: int | None = None
    encrypted: bool | None = None
    size: int | None = None  # original size
    stored_size: int | None = None  # total size of the volumes
    volumes: int = 0
    complete: bool | None = None  # every listed volume is present
    folder: str = ""  # folder under the root the file came from ("" = flat)

    @property
    def ratio(self) -> float | None:
        if self.size and self.stored_size is not None:
            return self.stored_size / self.size
        return None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "folder": self.folder,
            "path": str(self.path),
            "engine": self.engine,
            "format": self.format,
            "level": self.level,
            "encrypted": self.encrypted,
            "size": self.size,
            "stored_size": self.stored_size,
            "ratio": self.ratio,
            "volumes": self.volumes,
            "complete": self.complete,
        }
