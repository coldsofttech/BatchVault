"""Check archives against their manifests: the ``verify`` operation."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from batchvault.core.api._common import Progress
    from batchvault.core.locate import Target
    from batchvault.core.models import FileResult, RunResult


def verify(
    source: str | Path,
    *,
    against: str | Path | None = None,
    jobs: int = 0,
    seven_zip: str | Path | None = None,
    password: str | None = None,
    public_key: str | Path | None = None,
    progress: Progress | None = None,
) -> RunResult:
    """Check archives are complete and readable, without restoring anything.

    With a manifest, volume sizes and checksums are checked. ``against`` is a
    folder of the original files, compared by SHA-256 (manifest required); each
    original is looked for by name in that folder and then in its subfolders.
    ``public_key`` also requires a valid manifest signature.
    """
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from batchvault.core.api._common import (
        _log_result,
        _notify,
        _plan_event,
        _resolve,
        _split_jobs,
    )
    from batchvault.core.models import ProgressEvent, RunResult

    targets = _resolve(source, public_key)

    workers, _ = _split_jobs(jobs, len(targets))
    originals = Path(against).expanduser() if against else None

    _notify(progress, _plan_event(Path(source), targets))

    def work(target: Target) -> FileResult:
        size = target.entry.size if target.entry else 0
        _notify(progress, ProgressEvent("start", target.volume, size))
        outcome = _verify_one(target, originals, seven_zip, password)
        outcome.folder = target.entry.folder if target.entry else ""
        outcome.name = target.name
        _log_result("verify", outcome)
        _notify(progress, ProgressEvent("finish", target.volume, size, outcome.status))
        return outcome

    with ThreadPoolExecutor(max_workers=workers) as executor:
        outcomes = list(executor.map(work, targets))

    result = RunResult(entries=outcomes)
    if any(target.entry is None for target in targets):
        result.warnings.append(
            "No manifest found for some archives; only their internal integrity "
            "checks were run."
        )
    return result


def _find_originals(root: Path, folder: str, name: str) -> list[Path]:
    """The original called ``name``: at its stored folder under ``root``, else
    directly in ``root``, else anywhere below it."""
    from batchvault.core.api._common import _folder_path

    for candidate in (_folder_path(root, folder) / name, root / name):
        if candidate.is_file():
            return [candidate]
    return [path for path in root.rglob(name) if path.is_file()]


def _verify_one(
    target: Target,
    originals: Path | None,
    seven_zip: str | Path | None,
    password: str | None,
) -> FileResult:
    from batchvault.archive.manifest import hash_file
    from batchvault.core.api._common import ENCRYPTED_MESSAGE
    from batchvault.core.errors import BatchVaultError
    from batchvault.core.models import FileResult, Status
    from batchvault.engines.registry import engine_for_volume

    volume = target.volume
    entry = target.entry
    try:
        if entry:
            if entry.encrypted and not password:
                return FileResult(volume, Status.FAILED, ENCRYPTED_MESSAGE)
            problems = entry.check_volumes(target.base_dir or volume.parent)
            if problems:
                return FileResult(volume, Status.FAILED, "; ".join(problems))

        engine_for_volume(volume, seven_zip).test(volume, password=password)

        if originals and entry:
            candidates = _find_originals(originals, entry.folder, entry.name)
            if not candidates:
                message = f"Original not found under {originals}: {entry.name}"
                return FileResult(volume, Status.FAILED, message)
            if not any(hash_file(path) == entry.sha256 for path in candidates):
                message = f"{entry.name} differs from the archive."
                return FileResult(volume, Status.FAILED, message)

        if entry:
            message = "volumes, checksums and integrity test OK"
        else:
            message = "integrity test OK (no manifest, so checksums were not checked)"
        if originals and entry:
            message += "; matches the original"
        return FileResult(volume, Status.SUCCESS, message, entry.size if entry else 0)
    except (BatchVaultError, OSError) as exc:
        return FileResult(volume, Status.FAILED, str(exc))
