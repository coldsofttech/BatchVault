from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from batchvault import __version__
from batchvault.errors import ManifestError
from batchvault.names import validate_folder

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"
_CHUNK = 1024 * 1024


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _require(data: dict, key: str, kind: type, where: str):
    value = data.get(key)
    # bool is an int subclass; never accept it as a number.
    if not isinstance(value, kind) or isinstance(value, bool) and kind is int:
        raise ManifestError(f"{where}: '{key}' is missing or not a {kind.__name__}")
    return value


def _folder(value, where: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{where}: 'folder' must be a string")
    try:
        return validate_folder(value)
    except ValueError as exc:
        raise ManifestError(f"{where}: {exc}") from exc


@dataclass(frozen=True)
class VolumeInfo:
    file: str  # relative to the manifest folder, with forward slashes
    size: int
    sha256: str

    @classmethod
    def from_dict(cls, data, where: str) -> VolumeInfo:
        if not isinstance(data, dict):
            raise ManifestError(f"{where}: volume entry must be an object")
        return cls(
            file=_require(data, "file", str, where),
            size=_require(data, "size", int, where),
            sha256=_require(data, "sha256", str, where),
        )


@dataclass
class ManifestEntry:
    name: str
    size: int
    sha256: str
    engine: str
    format: str
    level: int
    volume_size: int
    volumes: list[VolumeInfo] = field(default_factory=list)
    encrypted: bool = False
    mtime_ns: int = 0  # source modification time when archived; 0 if unknown
    folder: str = ""  # where the file lived, relative to --root ("" = flat)

    @property
    def path(self) -> str:
        """The file's path relative to the root, with forward slashes."""
        return f"{self.folder}/{self.name}" if self.folder else self.name

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "sha256": self.sha256,
            "engine": self.engine,
            "format": self.format,
            "level": self.level,
            "volume_size": self.volume_size,
            "encrypted": self.encrypted,
            "mtime_ns": self.mtime_ns,
            "folder": self.folder,
            "volumes": [vars(volume) for volume in self.volumes],
        }

    @classmethod
    def from_dict(cls, data, index: int) -> ManifestEntry:
        where = f"entries[{index}]"
        if not isinstance(data, dict):
            raise ManifestError(f"{where}: must be an object")

        name = _require(data, "name", str, where)
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ManifestError(f"{where}: unsafe file name {name!r}")

        raw_volumes = _require(data, "volumes", list, where)
        if not raw_volumes:
            raise ManifestError(f"{where}: no volumes listed")

        return cls(
            name=name,
            size=_require(data, "size", int, where),
            sha256=_require(data, "sha256", str, where),
            engine=_require(data, "engine", str, where),
            format=_require(data, "format", str, where),
            level=_require(data, "level", int, where),
            volume_size=_require(data, "volume_size", int, where),
            volumes=[VolumeInfo.from_dict(v, where) for v in raw_volumes],
            encrypted=bool(data.get("encrypted", False)),
            mtime_ns=int(data.get("mtime_ns", 0) or 0),
            folder=_folder(data.get("folder", ""), where),
        )

    def check_volumes(self, base_dir: Path) -> list[str]:
        """Problems found with the volumes on disk; empty if all are intact."""
        problems = []
        for volume in self.volumes:
            path = base_dir / volume.file
            if not path.is_file():
                problems.append(f"missing volume {volume.file}")
            elif path.stat().st_size != volume.size:
                problems.append(f"volume {volume.file} has the wrong size")
            elif hash_file(path) != volume.sha256:
                problems.append(f"volume {volume.file} fails its checksum")
        return problems


@dataclass
class Manifest:
    entries: dict[str, ManifestEntry] = field(default_factory=dict)
    created: str = ""

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "created": self.created or datetime.now(UTC).isoformat(timespec="seconds"),
            "batchvault_version": __version__,
            "entries": [entry.to_dict() for entry in self.entries.values()],
        }

    @classmethod
    def from_dict(cls, data) -> Manifest:
        if not isinstance(data, dict):
            raise ManifestError("Manifest must be a JSON object")

        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ManifestError(
                f"Unsupported manifest schema_version {version!r} "
                f"(this version reads {SCHEMA_VERSION})."
            )

        entries = {}
        for index, raw in enumerate(_require(data, "entries", list, "manifest")):
            entry = ManifestEntry.from_dict(raw, index)
            if entry.path in entries:
                raise ManifestError(f"Duplicate entry for {entry.path!r}")
            entries[entry.path] = entry

        return cls(entries=entries, created=str(data.get("created", "")))

    @classmethod
    def from_bytes(cls, raw: bytes, source: Path) -> Manifest:
        """Parse manifest bytes already read from ``source``."""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"Manifest {source} is not valid JSON: {exc}") from exc

        try:
            return cls.from_dict(data)
        except ManifestError as exc:
            raise ManifestError(f"{source}: {exc}") from exc

    @classmethod
    def load(cls, path: Path) -> Manifest:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
        return cls.from_bytes(raw, path)

    def save(self, path: Path) -> None:
        """Write atomically so an interrupted run cannot leave a half-written file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"

        # A fresh, unpredictable, owner-only file (not a fixed ".tmp" name that
        # someone could pre-create as a link); the manifest lists file names.
        fd, name = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp"
        )
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
