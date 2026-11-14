"""Describe archives: ``inspect``, ``list_files`` and ``list_archives``."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from batchvault.core.models import FileEntry, InspectEntry


def inspect(source: str | Path) -> list[InspectEntry]:
    """Describe archives from their manifest (or file names when there is none)."""
    from batchvault.archive.volumes import existing_volumes
    from batchvault.core.locate import resolve_targets
    from batchvault.core.models import InspectEntry

    found = []
    for target in resolve_targets(source):
        entry = target.entry
        if entry is None:
            volumes = existing_volumes(
                target.volume.parent, target.volume.name.rsplit(".", 1)[0]
            )
            found.append(
                InspectEntry(
                    name=target.name,
                    path=target.volume,
                    engine="7zip" if ".7z." in target.volume.name.lower() else "native",
                    stored_size=sum(v.stat().st_size for v in volumes),
                    volumes=len(volumes),
                )
            )
            continue

        base_dir = target.base_dir or target.volume.parent
        paths = [base_dir / volume.file for volume in entry.volumes]
        found.append(
            InspectEntry(
                name=entry.name,
                folder=entry.folder,
                path=paths[0],
                engine=entry.engine,
                format=entry.format,
                level=entry.level,
                encrypted=entry.encrypted,
                size=entry.size,
                stored_size=sum(volume.size for volume in entry.volumes),
                volumes=len(paths),
                complete=all(path.is_file() for path in paths),
            )
        )
    return found


def list_files(
    folder: str | Path,
    *,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[FileEntry]:
    """Files under ``folder`` (recursively), filtered by include/exclude patterns.

    Patterns match against the path relative to ``folder``.
    """
    from pathlib import Path

    from batchvault.core.errors import InputError
    from batchvault.core.filters import PathFilter
    from batchvault.core.models import FileEntry

    root = Path(folder).expanduser()
    if not root.is_dir():
        raise InputError(f"Folder not found: {root}")

    path_filter = PathFilter(include, exclude)
    # Record the folder's parent so that packing the listing keeps the folder's
    # own name (Resources/...), not just what is inside it.
    recorded = root.resolve().parent
    return [
        FileEntry(path.resolve(), path.stat().st_size, recorded)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path_filter.matches(path.relative_to(root).as_posix())
    ]


def list_archives(folder: str | Path) -> list[FileEntry]:
    """Archives under ``folder``: from its manifest if present, else by scanning."""
    from pathlib import Path

    from batchvault.core.errors import InputError
    from batchvault.core.locate import resolve_targets
    from batchvault.core.models import FileEntry

    root = Path(folder).expanduser()
    if not root.is_dir():
        raise InputError(f"Folder not found: {root}")

    try:
        targets = resolve_targets(root)
    except InputError:
        return []
    return [
        FileEntry(target.volume.resolve(), target.entry.size if target.entry else None)
        for target in targets
    ]
