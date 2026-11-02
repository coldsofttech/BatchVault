"""Run journals: the state that lets ``--resume`` continue an interrupted run.

A journal is a JSON Lines file kept in the output folder while a run is in
progress and removed when the run finishes without failures. The first line is a
header holding every setting of the run, so ``--resume`` needs no other options.
Each later line records one file that was finished (and, for ``unpack``, placed
and verified).

Lines are appended and flushed to disk one at a time, so a crash or power cut
loses at most the line being written; a torn last line is ignored. Passwords and
keys are never written.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from batchvault import __version__
from batchvault.errors import ResumeError
from batchvault.manifest import ManifestEntry

PACK_JOURNAL = ".batchvault-pack.jsonl"
UNPACK_JOURNAL = ".batchvault-unpack.jsonl"
VERSION = 1


def _need(data: dict, key: str, kind: type | tuple[type, ...], where: str):
    value = data.get(key)
    if isinstance(value, bool) and kind is not bool:
        value = None
    if not isinstance(value, kind):
        raise ResumeError(f"The journal is damaged: {where} has no valid '{key}'.")
    return value


def _strings(data: dict, key: str, where: str) -> list[str]:
    values = _need(data, key, list, where)
    if not all(isinstance(value, str) for value in values):
        raise ResumeError(f"The journal is damaged: {where} '{key}' must be text.")
    return values


@dataclass(frozen=True)
class PackSettings:
    """Everything that shaped a ``pack`` run (no secrets)."""

    sources: list[tuple[str, str]]  # (absolute path, stored folder)
    engine: str  # the engine that was used: "native" or "7zip"
    format: str | None
    level: int | str  # 0-9 or "auto"
    volume_size: int
    jobs: int
    retries: int
    include: list[str]
    exclude: list[str]
    manifest: str
    update: bool
    verify: bool
    delete_source: bool
    strict: bool
    readme: bool
    encrypted: bool
    signed: bool

    def to_dict(self) -> dict:
        data = dict(vars(self))
        data["sources"] = [list(pair) for pair in self.sources]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> PackSettings:
        where = "the settings"
        raw_sources = _need(data, "sources", list, where)
        sources = []
        for pair in raw_sources:
            if (
                not isinstance(pair, list)
                or len(pair) != 2
                or not all(isinstance(part, str) for part in pair)
            ):
                raise ResumeError("The journal is damaged: a source is malformed.")
            sources.append((pair[0], pair[1]))

        level = data.get("level")
        if isinstance(level, bool) or not isinstance(level, int | str):
            raise ResumeError("The journal is damaged: the settings have no 'level'.")
        file_format = data.get("format")
        if file_format is not None and not isinstance(file_format, str):
            raise ResumeError("The journal is damaged: 'format' must be text.")

        return cls(
            sources=sources,
            engine=_need(data, "engine", str, where),
            format=file_format,
            level=level,
            volume_size=_need(data, "volume_size", int, where),
            jobs=_need(data, "jobs", int, where),
            retries=_need(data, "retries", int, where),
            include=_strings(data, "include", where),
            exclude=_strings(data, "exclude", where),
            manifest=_need(data, "manifest", str, where),
            update=_need(data, "update", bool, where),
            verify=_need(data, "verify", bool, where),
            delete_source=_need(data, "delete_source", bool, where),
            strict=_need(data, "strict", bool, where),
            readme=_need(data, "readme", bool, where),
            encrypted=_need(data, "encrypted", bool, where),
            signed=_need(data, "signed", bool, where),
        )


@dataclass(frozen=True)
class UnpackSettings:
    """Everything that shaped an ``unpack`` run (no secrets)."""

    source: str  # absolute path of what was given as SOURCE
    manifests: dict[str, str]  # manifest path -> SHA-256 of its bytes
    signed: bool  # a public key was required

    def to_dict(self) -> dict:
        return dict(vars(self))

    @classmethod
    def from_dict(cls, data: dict) -> UnpackSettings:
        where = "the settings"
        manifests = _need(data, "manifests", dict, where)
        if not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in manifests.items()
        ):
            raise ResumeError("The journal is damaged: 'manifests' must be text.")
        return cls(
            source=_need(data, "source", str, where),
            manifests=manifests,
            signed=_need(data, "signed", bool, where),
        )


@dataclass
class Loaded:
    """A journal read back from disk."""

    created: str
    version: str  # BatchVault version that wrote it
    settings: dict
    records: list[dict] = field(default_factory=list)
    good_size: int = 0  # bytes up to the end of the last complete line


def read(path: Path, command: str) -> Loaded:
    """Read a journal; raise ``ResumeError`` if there is none or it is unusable.

    A torn last line (the run died while writing it) is ignored; damage anywhere
    else means the journal cannot be trusted.
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ResumeError(
            f"Nothing to resume: {path} does not exist. "
            "Resume continues a run that was interrupted or had failures; "
            f"run {command} without --resume to start."
        ) from None
    except OSError as exc:
        raise ResumeError(f"Cannot read the journal {path}: {exc}") from exc

    lines = raw.split(b"\n")
    complete = lines[:-1]  # the text after the last newline was never finished
    good_size = len(raw) - len(lines[-1])
    if not complete:
        raise ResumeError(f"The journal {path} is empty or damaged; cannot resume.")

    parsed = []
    for number, line in enumerate(complete, 1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ResumeError(
                f"The journal {path} is damaged at line {number}; cannot resume."
            ) from None
        if not isinstance(value, dict):
            raise ResumeError(
                f"The journal {path} is damaged at line {number}; cannot resume."
            )
        parsed.append(value)

    header = parsed[0]
    if header.get("kind") != "header" or header.get("version") != VERSION:
        raise ResumeError(
            f"The journal {path} was not written by this version of BatchVault "
            "(unknown format); cannot resume."
        )
    if header.get("command") != command:
        raise ResumeError(
            f"The journal {path} belongs to {header.get('command')!r}, not {command!r}."
        )
    settings = header.get("settings")
    if not isinstance(settings, dict):
        raise ResumeError(f"The journal {path} has no settings; cannot resume.")

    return Loaded(
        created=str(header.get("created", "")),
        version=str(header.get("batchvault_version", "")),
        settings=settings,
        records=parsed[1:],
        good_size=good_size,
    )


def load_pack(path: Path) -> tuple[PackSettings, dict[str, ManifestEntry], Loaded]:
    """The settings and finished files of an interrupted ``pack``."""
    loaded = read(path, "pack")
    settings = PackSettings.from_dict(loaded.settings)

    finished: dict[str, ManifestEntry] = {}
    for index, record in enumerate(loaded.records):
        if record.get("kind") != "file" or not isinstance(record.get("key"), str):
            raise ResumeError(f"The journal {path} has an unknown record.")
        try:
            finished[record["key"]] = ManifestEntry.from_dict(
                record.get("entry"), index
            )
        except Exception as exc:  # a malformed entry: the journal is not trustworthy
            raise ResumeError(f"The journal {path} has a damaged entry: {exc}") from exc
    return settings, finished, loaded


def load_unpack(path: Path) -> tuple[UnpackSettings, dict[str, str], Loaded]:
    """The settings and placed files (path -> SHA-256) of an interrupted ``unpack``."""
    loaded = read(path, "unpack")
    settings = UnpackSettings.from_dict(loaded.settings)

    placed: dict[str, str] = {}
    for record in loaded.records:
        name, digest = record.get("path"), record.get("sha256")
        if record.get("kind") != "file" or not isinstance(name, str):
            raise ResumeError(f"The journal {path} has an unknown record.")
        if not isinstance(digest, str):
            raise ResumeError(f"The journal {path} has a damaged record.")
        placed[name] = digest
    return settings, placed, loaded


class Journal:
    """An open journal that finished files are appended to (thread-safe)."""

    def __init__(self, path: Path, handle):
        self.path = path
        self._handle = handle
        self._lock = threading.Lock()
        self.appended = 0  # records written by this run

    @classmethod
    def create(cls, path: Path, command: str, settings: dict) -> Journal:
        """Start a new journal, replacing any old one."""
        header = {
            "kind": "header",
            "version": VERSION,
            "command": command,
            "batchvault_version": __version__,
            "created": datetime.now(UTC).isoformat(timespec="seconds"),
            "settings": settings,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only, written whole before the name appears (as for the manifest).
        fd, name = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp"
        )
        temp = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(header, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return cls._open(path)

    @classmethod
    def reopen(cls, path: Path, good_size: int) -> Journal:
        """Continue an existing journal, dropping a torn last line first."""
        try:
            os.truncate(path, good_size)
        except OSError as exc:
            raise ResumeError(f"Cannot continue the journal {path}: {exc}") from exc
        return cls._open(path)

    @classmethod
    def _open(cls, path: Path) -> Journal:
        handle = path.open("a", encoding="utf-8", newline="\n")
        return cls(path, handle)

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self._handle.write(line)
            self._handle.flush()
            os.fsync(self._handle.fileno())
            self.appended += 1

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.close()

    def remove(self) -> None:
        """Close and delete the journal (the run finished)."""
        self.close()
        self.path.unlink(missing_ok=True)
