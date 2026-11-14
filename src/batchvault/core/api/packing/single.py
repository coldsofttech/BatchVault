"""Pack one file into volumes and check the result."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os
    from pathlib import Path

    from batchvault.archive.manifest import Manifest, ManifestEntry
    from batchvault.core.api._common import Progress
    from batchvault.core.models import FileResult
    from batchvault.core.options import PackOptions
    from batchvault.engines.base import Engine


def _pack_one(
    backend: Engine,
    source: Path,
    folder: str,
    out_root: Path,
    manifest_dir: Path,
    options: PackOptions,
    verify: bool,
    progress: Progress | None,
    known: Manifest | None,
    retries: int,
    auto_level: bool = False,
    journaled: ManifestEntry | None = None,
    resume: bool = False,
) -> tuple[FileResult, ManifestEntry | None]:
    import dataclasses

    from batchvault.archive.manifest import ManifestEntry, VolumeInfo, hash_file
    from batchvault.core.api._common import _folder_path, _log_result, _notify, _retry
    from batchvault.core.api.packing.plan import (
        _file_options,
        _format_name,
        _pack_summary,
        _relative,
        _up_to_date,
    )
    from batchvault.core.errors import BatchVaultError, EngineError, OutputExistsError
    from batchvault.core.models import FileResult, ProgressEvent, Status
    from batchvault.core.options import format_size

    stat = source.stat()
    size = stat.st_size
    _notify(progress, ProgressEvent("start", source, size))
    options, choice = _file_options(options, source, auto_level)

    entry = None
    try:
        key = f"{folder}/{source.name}" if folder else source.name
        previous = known.entries.get(key) if known is not None else None
        redone = ""
        if journaled is not None:
            redone = _reuse_problem(journaled, stat, manifest_dir)

        if journaled is not None and not redone:
            # An earlier run finished this file and nothing has changed since.
            entry = journaled
            volumes = [manifest_dir / volume.file for volume in journaled.volumes]
            stored = sum(volume.size for volume in journaled.volumes)
            outcome = FileResult(
                source,
                Status.SUCCESS,
                f"Resumed: already archived, {len(volumes)} "
                f"volume{'' if len(volumes) == 1 else 's'}, "
                f"{format_size(stored)}, checksums verified",
                size,
                volumes,
                archive=volumes[0],
                stored_size=stored,
                level=journaled.level,
                resumed=True,
            )
        elif previous and _up_to_date(previous, stat, backend, options, manifest_dir):
            outcome = FileResult(source, Status.SKIPPED, "Up to date.", size)
        else:
            # In update mode a changed file replaces its old archive; a resumed
            # run replaces what an earlier run left (its own output).
            file_options = options
            if known is not None or resume:
                file_options = dataclasses.replace(options, force=True)

            destination = _folder_path(out_root, folder) / source.name
            volumes = _retry(
                lambda: backend.pack(source, destination, file_options),
                retries,
                (OSError, EngineError),
            )
            entry = ManifestEntry(
                name=source.name,
                folder=folder,
                size=size,
                sha256=hash_file(source),
                engine=backend.name,
                format=_format_name(backend, options),
                level=options.level,
                volume_size=options.volume_size,
                volumes=[
                    VolumeInfo(
                        _relative(volume, manifest_dir),
                        volume.stat().st_size,
                        hash_file(volume),
                    )
                    for volume in volumes
                ],
                encrypted=bool(options.password),
                mtime_ns=stat.st_mtime_ns,
            )
            if verify:
                backend.test(volumes[0], password=options.password)
            stored = sum(volume.size for volume in entry.volumes)
            outcome = FileResult(
                source,
                Status.SUCCESS,
                _pack_summary(size, stored, len(volumes), verify, choice),
                size,
                volumes,
                archive=volumes[0],
                stored_size=stored,
                level=options.level,
            )
            if redone:
                outcome.redone = redone
                outcome.message += f"; redone: {redone}"
    except OutputExistsError as exc:
        outcome = FileResult(source, Status.SKIPPED, str(exc), size)
    except (BatchVaultError, OSError) as exc:
        outcome, entry = FileResult(source, Status.FAILED, str(exc), size), None

    outcome.folder = folder
    outcome.name = source.name
    _log_result("pack", outcome)
    _notify(
        progress,
        ProgressEvent("finish", source, size, outcome.status, resumed=outcome.resumed),
    )
    return outcome, entry


def _reuse_problem(
    entry: ManifestEntry, stat: os.stat_result, manifest_dir: Path
) -> str:
    """Why a file an earlier run finished cannot be reused; empty if it can.

    The volumes are checked against their checksums, never trusted by name or
    size alone: a power cut can leave files that exist but are cut short.
    """
    if stat.st_size != entry.size or not entry.mtime_ns:
        return "the source file changed"
    if stat.st_mtime_ns != entry.mtime_ns:
        return "the source file changed"
    problems = entry.check_volumes(manifest_dir)
    return problems[0] if problems else ""


def _roundtrip_problem(
    backend: Engine, outcome: FileResult, entry: ManifestEntry, password: str | None
) -> str | None:
    """Restore the archive and compare it with the original; None if identical."""
    import tempfile
    from pathlib import Path

    from batchvault.archive.manifest import hash_file
    from batchvault.archive.volumes import STAGING_PREFIX
    from batchvault.core.errors import BatchVaultError

    try:
        parent = outcome.volumes[0].parent
        with tempfile.TemporaryDirectory(dir=parent, prefix=STAGING_PREFIX) as tmp:
            restored = backend.unpack(
                outcome.volumes[0],
                Path(tmp),
                force=True,
                password=password,
                max_bytes=entry.size,
            )
            if [path.name for path in restored] != [entry.name]:
                return "the archive does not contain the expected file"
            if hash_file(restored[0]) != entry.sha256:
                return "the restored file differs from the original"
        if hash_file(outcome.source) != entry.sha256:
            return "the original changed while it was being archived"
    except (BatchVaultError, OSError) as exc:
        return str(exc)
    return None
