"""Restore one file from its volumes and place it in the destination."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from batchvault.archive import resume as journals
    from batchvault.archive.manifest import ManifestEntry
    from batchvault.core.locate import Target
    from batchvault.core.models import FileResult


def _destination_folder(out_root: Path, folder: str) -> Path:
    """Where files stored under ``folder`` are restored, kept inside ``out_root``."""
    import os

    from batchvault.core.api._common import _folder_path
    from batchvault.core.errors import InputError
    from batchvault.core.names import unsafe_on_windows

    destination = _folder_path(out_root, folder)
    if not folder:
        return destination

    if os.name == "nt" and any(unsafe_on_windows(part) for part in folder.split("/")):
        raise InputError(f"The stored folder {folder!r} has names Windows cannot use.")

    root = out_root.resolve()
    if root not in destination.resolve().parents:
        raise InputError(f"The stored folder {folder!r} points outside the output.")
    return destination


def _restore_one(
    target: Target,
    out_root: Path,
    force: bool,
    update: bool,
    seven_zip: str | Path | None,
    password: str | None,
    retries: int,
    max_bytes: int | None,
    resume: bool = False,
    placed: dict[str, str] | None = None,
    journal: journals.Journal | None = None,
) -> FileResult:
    import os
    import tempfile
    from pathlib import Path

    from batchvault.archive.manifest import hash_file
    from batchvault.archive.volumes import STAGING_PREFIX
    from batchvault.core.api._common import ENCRYPTED_MESSAGE, _retry
    from batchvault.core.errors import BatchVaultError
    from batchvault.core.models import FileResult, Status
    from batchvault.core.options import format_size
    from batchvault.engines.registry import engine_for_volume

    volume = target.volume
    entry = target.entry
    try:
        limit = max_bytes
        if entry:  # the manifest says how big the file is; never accept more
            limit = entry.size if limit is None else min(limit, entry.size)

        folder = entry.folder if entry else ""
        destination_dir = _destination_folder(out_root, folder)
        destination = destination_dir / target.name
        redone = ""
        if resume and entry is not None and destination.exists():
            if _matches(destination, entry):
                return FileResult(
                    volume,
                    Status.SUCCESS,
                    f"Resumed: already restored, {format_size(entry.size)}, "
                    "checksum verified",
                    entry.size,
                    restored=[destination],
                    resumed=True,
                )
            if destination.relative_to(out_root).as_posix() not in (placed or {}):
                return FileResult(
                    volume,
                    Status.SKIPPED,
                    f"{target.name} exists and differs from the archive, and an "
                    "earlier run did not write it, so it was left alone. "
                    "Use force to overwrite.",
                )
            redone = "the restored file no longer matches its checksum"
        elif destination.exists() and not force:
            if not update:
                return FileResult(
                    volume,
                    Status.SKIPPED,
                    f"{target.name} already exists. Use force to overwrite.",
                )
            if entry is None:
                return FileResult(
                    volume,
                    Status.SKIPPED,
                    f"{target.name} exists and there is no manifest to compare with.",
                )
            if _matches(destination, entry):
                return FileResult(volume, Status.SKIPPED, "Already up to date.")

        if entry:
            if entry.encrypted and not password:
                return FileResult(volume, Status.FAILED, ENCRYPTED_MESSAGE)
            problems = entry.check_volumes(target.base_dir or volume.parent)
            if problems:
                return FileResult(volume, Status.FAILED, "; ".join(problems))

        backend = engine_for_volume(volume, seven_zip)
        out_root.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=out_root, prefix=STAGING_PREFIX) as tmp:
            restored = _retry(
                lambda: backend.unpack(
                    volume, Path(tmp), force=True, password=password, max_bytes=limit
                ),
                retries,
                (OSError,),
            )

            if entry:
                names = [path.name for path in restored]
                # macOS may hand back the same name in another Unicode form.
                if [_nfc(n) for n in names] != [_nfc(entry.name)]:
                    return FileResult(
                        volume,
                        Status.FAILED,
                        f"Archive contains {names}, manifest expects {entry.name!r}.",
                    )
                if hash_file(restored[0]) != entry.sha256:
                    return FileResult(
                        volume,
                        Status.FAILED,
                        f"Restored {entry.name} does not match its checksum.",
                    )

            destination_dir.mkdir(parents=True, exist_ok=True)
            final = []
            for path in restored:
                name = entry.name if entry else path.name
                os.replace(path, destination_dir / name)
                final.append(destination_dir / name)

        if journal is not None and entry is not None:
            journal.append(
                {
                    "kind": "file",
                    "path": final[0].relative_to(out_root).as_posix(),
                    "sha256": entry.sha256,
                    "size": entry.size,
                }
            )

        size = sum(path.stat().st_size for path in final)
        checked = "checksum verified" if entry else "checksum not verified"
        message = f"{format_size(size)} restored, {checked}"
        if redone:
            message += f"; redone: {redone}"
        return FileResult(
            volume, Status.SUCCESS, message, size, restored=final, redone=redone
        )
    except (BatchVaultError, OSError) as exc:
        return FileResult(volume, Status.FAILED, str(exc))


def _nfc(name: str) -> str:
    import unicodedata

    return unicodedata.normalize("NFC", name)


def _matches(path: Path, entry: ManifestEntry) -> bool:
    from batchvault.archive.manifest import hash_file

    return (
        path.is_file()
        and path.stat().st_size == entry.size
        and (hash_file(path) == entry.sha256)
    )
