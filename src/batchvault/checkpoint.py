"""Checkpoints inside one file: where a ``pack`` can pick up after an interruption.

While the native engine builds a file's volumes it reports, to the run journal,
every finished volume (size and SHA-256) and, after each compressed block, a
checkpoint: how many blocks are done, how far the output has got, and the hash of
the part of the current volume written so far. The data is flushed to disk before
the checkpoint is written, so a checkpoint never describes bytes that are not on
disk.

On resume the newest checkpoint whose data still matches its hashes is used; the
volumes after it are discarded and the build continues from that block. Anything
that does not match is never trusted: the next older checkpoint is tried, and
failing that the file starts over.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from batchvault.manifest import hash_file
from batchvault.volumes import STAGING_PREFIX, Adopted

if TYPE_CHECKING:
    from batchvault.resume import Journal, Partial

_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class Spec:
    """What a stream was built from: a checkpoint is only valid for the same one."""

    fmt: str
    level: int
    block_size: int
    volume_size: int
    encrypted: bool
    size: int  # of the source file
    mtime_ns: int  # of the source file

    def to_dict(self) -> dict:
        return dict(vars(self))


@dataclass
class Resume:
    """Where a file's build continues."""

    blocks: int  # compressed blocks already written
    adopted: Adopted
    header: bytes  # the encryption header of the archive ("" when plain)
    volumes: list[Path]  # every kept volume, in order


def _prefix_hash(path: Path, size: int):
    """SHA-256 state of the first ``size`` bytes of ``path``; None if too short."""
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            left = size
            while left:
                data = handle.read(min(_CHUNK, left))
                if not data:
                    return None
                digest.update(data)
                left -= len(data)
    except OSError:
        return None
    return digest


class FileCheckpoints:
    """The journal records and resume logic for one file being packed."""

    def __init__(self, key: str, journal: Journal | None, partial: Partial | None):
        self.key = key
        self._journal = journal
        self._volumes: dict[int, tuple[int, str]] = (
            dict(partial.volumes) if partial else {}
        )
        self._checkpoints: list[dict] = list(partial.checkpoints) if partial else []
        self.continued_bytes = 0  # source bytes an earlier attempt had finished
        self.kept_volumes = 0

    @property
    def staging_name(self) -> str:
        """A stable name, so a later attempt finds the folder an earlier one left."""
        digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:16]
        return f"{STAGING_PREFIX}resume-{digest}"

    # -- recording (called by the engine while it builds)

    def volume_done(self, index: int, size: int, digest: str) -> None:
        self._volumes[index] = (size, digest)
        self._write(
            {"kind": "volume", "key": self.key, "index": index, "size": size,
             "sha256": digest}
        )  # fmt: skip

    def checkpoint(
        self, spec: Spec, blocks: int, out: int, prefix: str, header: bytes
    ) -> None:
        record = {
            "kind": "checkpoint",
            "key": self.key,
            "spec": spec.to_dict(),
            "blocks": blocks,
            "out": out,
            "prefix": prefix,
            "header": header.hex(),
        }
        self._checkpoints.append(record)
        self._write(record)

    def _write(self, record: dict) -> None:
        if self._journal is not None:
            self._journal.append(record)

    def _forget(self) -> None:
        """Start the file over: nothing recorded so far may be used again."""
        if self._volumes or self._checkpoints:
            self._volumes.clear()
            self._checkpoints.clear()
            self._write({"kind": "restart", "key": self.key})

    def mark_continued(self, volumes: int, source_bytes: int) -> None:
        self.kept_volumes = volumes
        self.continued_bytes = source_bytes

    # -- resuming

    def find_resume(self, staging: Path, base: str, spec: Spec) -> Resume | None:
        """The newest checkpoint still matching the data on disk; None to start over."""
        if not self._checkpoints:
            self._forget()
            _empty(staging)
            return None

        def path(index: int) -> Path:
            return staging / f"{base}.{index:03d}"

        verdicts: dict[int, bool] = {}

        def intact(index: int) -> bool:
            if index not in verdicts:
                known = self._volumes.get(index)
                file = path(index)
                verdicts[index] = bool(
                    known
                    and known[0] == spec.volume_size
                    and file.is_file()
                    and file.stat().st_size == spec.volume_size
                    and hash_file(file) == known[1]
                )
            return verdicts[index]

        for record in reversed(self._checkpoints):
            if record.get("spec") != spec.to_dict():
                continue
            blocks, out = record["blocks"], record["out"]
            full, offset = divmod(out, spec.volume_size)
            if blocks < 1 or not all(intact(i) for i in range(1, full + 1)):
                continue

            partial_hash = None
            if offset:
                partial_hash = _prefix_hash(path(full + 1), offset)
                if partial_hash is None or partial_hash.hexdigest() != record["prefix"]:
                    continue

            kept = [path(i) for i in range(1, full + 1)]
            if offset:
                kept.append(path(full + 1))
                os.truncate(path(full + 1), offset)  # drop what came after
            _empty(staging, keep=set(kept))
            return Resume(
                blocks=blocks,
                adopted=Adopted(
                    complete=kept[:full],
                    partial=path(full + 1) if offset else None,
                    partial_hash=partial_hash,
                    partial_size=offset,
                ),
                header=bytes.fromhex(record["header"]),
                volumes=kept,
            )

        self._forget()
        _empty(staging)
        return None


def _empty(folder: Path, keep: set[Path] | None = None) -> None:
    """Remove everything in ``folder`` except the paths in ``keep``."""
    keep = keep or set()
    if not folder.is_dir():
        return
    for child in folder.iterdir():
        if child in keep:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
