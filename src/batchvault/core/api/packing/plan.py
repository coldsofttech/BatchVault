"""Decide what to pack and whether an existing archive can be reused."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import os

    from batchvault.archive.manifest import Manifest, ManifestEntry
    from batchvault.core.levels import LevelChoice
    from batchvault.core.models import RunResult
    from batchvault.core.options import PackOptions
    from batchvault.engines.base import Engine


Confirm = Callable[[list[Path]], bool]


def _relative(path: Path, base: Path) -> str:
    import os

    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # different drive on Windows
        return str(path)


def _format_name(backend: Engine, options: PackOptions) -> str:
    return "7z" if backend.name == "7zip" else options.format


def _up_to_date(
    entry: ManifestEntry,
    stat: os.stat_result,
    backend: Engine,
    options: PackOptions,
    manifest_dir: Path,
) -> bool:
    """True if an archive already matches the file and the requested settings."""
    if not entry.mtime_ns or entry.mtime_ns != stat.st_mtime_ns:
        return False
    if entry.size != stat.st_size:
        return False
    if (
        entry.engine != backend.name
        or entry.format != _format_name(backend, options)
        or entry.level != options.level
        or entry.volume_size != options.volume_size
        or entry.encrypted != bool(options.password)
    ):
        return False

    for volume in entry.volumes:
        path = manifest_dir / volume.file
        if not path.is_file() or path.stat().st_size != volume.size:
            return False
    return True


def _check_resume_password(
    backend: Engine,
    journaled: dict[str, ManifestEntry],
    manifest_dir: Path,
    password: str,
) -> None:
    """Refuse a password that does not open the archives made by the earlier run.

    Reusing files made with another password would leave an archive that cannot
    be restored with a single password. The smallest intact encrypted archive
    is tested, which keeps the check cheap.
    """
    from batchvault.core.errors import EngineError, ResumeError

    for entry in sorted(journaled.values(), key=lambda item: item.size):
        if not entry.encrypted or entry.check_volumes(manifest_dir):
            continue
        try:
            backend.test(manifest_dir / entry.volumes[0].file, password=password)
        except EngineError as exc:
            raise ResumeError(
                "The password does not match the archives made by the earlier "
                f"run ({exc})."
            ) from exc
        return


def _file_options(
    options: PackOptions, source: Path, auto_level: bool
) -> tuple[PackOptions, LevelChoice | None]:
    """The options for one file; with ``--level auto`` the level suits its type."""
    import dataclasses

    from batchvault.core.levels import choose_level

    if not auto_level or options.format == "store":  # store ignores the level
        return options, None
    choice = choose_level(source)
    return dataclasses.replace(options, level=choice.level), choice


def _plan_pack(
    backend: Engine,
    sources: list[Path],
    folders: dict[Path, str],
    out_root: Path,
    manifest_dir: Path,
    options: PackOptions,
    warnings: list[str],
    known: Manifest | None,
    auto_level: bool = False,
) -> RunResult:
    from batchvault.archive.volumes import existing_volumes
    from batchvault.core.api._common import _folder_path
    from batchvault.core.models import FileResult, RunResult, Status

    result = RunResult(warnings=warnings, engine=backend.name)
    suffix = backend.capabilities.archive_suffix

    for source in sources:
        stat = source.stat()
        folder = folders[source]
        existing = existing_volumes(
            _folder_path(out_root, folder) / source.name, f"{source.name}{suffix}"
        )
        key = f"{folder}/{source.name}" if folder else source.name
        entry = known.entries.get(key) if known else None
        file_options, choice = _file_options(options, source, auto_level)

        if entry and _up_to_date(entry, stat, backend, file_options, manifest_dir):
            outcome = FileResult(
                source, Status.SKIPPED, "Would skip: up to date.", stat.st_size
            )
        elif existing and not (file_options.force or known is not None):
            outcome = FileResult(
                source,
                Status.SKIPPED,
                "Would skip: archive already exists.",
                stat.st_size,
            )
        else:
            message = "Would update" if existing else "Would archive"
            if choice:
                message += f" at level {choice.level} ({choice.kind})"
            outcome = FileResult(source, Status.SUCCESS, message + ".", stat.st_size)
        outcome.folder = folder
        outcome.level = file_options.level
        outcome.name = source.name
        result.entries.append(outcome)
    return result


def _pack_summary(
    size: int, stored: int, volumes: int, verified: bool, choice: LevelChoice | None
) -> str:
    """e.g. ``3 volumes, 12.3 MiB -> 4.1 MiB (33%), level 6 (text), verified``."""
    from batchvault.core.options import format_size

    sizes = f"{format_size(size)} -> {format_size(stored)}"
    if size:
        sizes += f" ({stored / size:.0%})"
    parts = [f"{volumes} volume{'' if volumes == 1 else 's'}", sizes]
    if choice:
        parts.append(f"level {choice.level} ({choice.kind})")
    if verified:
        parts.append("verified")
    return ", ".join(parts)
