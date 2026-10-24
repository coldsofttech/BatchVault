"""Public Python API. The command-line interface is a thin wrapper over this."""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile
import time
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from batchvault import signing
from batchvault.crypto import require_cryptography
from batchvault.engines.base import Engine
from batchvault.engines.registry import engine_for_volume, get_engine, option_warnings
from batchvault.errors import (
    BatchVaultError,
    EngineError,
    InputError,
    ManifestError,
    OptionsError,
    OutputExistsError,
    SignatureError,
)
from batchvault.filters import PathFilter
from batchvault.inputs import gather_sources
from batchvault.levels import AUTO, LevelChoice, choose_level, parse_level
from batchvault.locate import Target, resolve_targets
from batchvault.manifest import (
    MANIFEST_NAME,
    Manifest,
    ManifestEntry,
    VolumeInfo,
    hash_file,
)
from batchvault.models import (
    FileEntry,
    FileResult,
    InspectEntry,
    ProgressEvent,
    RunResult,
    Status,
)
from batchvault.names import unsafe_on_windows
from batchvault.options import PackOptions, format_size, parse_size
from batchvault.volumes import STAGING_PREFIX, cleanup_staging, existing_volumes

Progress = Callable[[ProgressEvent], None]
Confirm = Callable[[list[Path]], bool]

log = logging.getLogger(__name__)

RETRY_BASE_DELAY = 1.0
RETRY_MAX_DELAY = 30.0
ENCRYPTED_MESSAGE = "Archive is encrypted; a password is required."


def _log_result(action: str, outcome: FileResult) -> None:
    level = logging.WARNING if outcome.status == Status.FAILED else logging.INFO
    log.log(
        level, "%s %s: %s %s", action, outcome.source, outcome.status, outcome.message
    )


def _split_jobs(jobs: int | None, files: int) -> tuple[int, int]:
    """Split one thread budget into (files in parallel, threads per file).

    A budget of 0 means no worker threads: one file at a time, compressed in
    the calling thread.
    """
    total = jobs or 0
    if total < 0:
        raise InputError("jobs cannot be negative")
    if total == 0:
        return 1, 0
    workers = max(1, min(files, total))
    return workers, max(1, total // workers)


def _notify(progress: Progress | None, event: ProgressEvent) -> None:
    if progress:
        progress(event)


def _relative(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:  # different drive on Windows
        return str(path)


def _retry(call, retries: int, retry_on: tuple[type[Exception], ...]):
    """Run ``call()``, retrying transient failures with exponential backoff."""
    attempt = 0
    while True:
        try:
            return call()
        except FileNotFoundError:
            raise
        except retry_on as exc:
            if attempt >= retries:
                raise
            delay = min(RETRY_BASE_DELAY * 2**attempt, RETRY_MAX_DELAY)
            log.warning(
                "Attempt %d failed (%s); retrying in %.1fs", attempt + 1, exc, delay
            )
            time.sleep(delay)
            attempt += 1


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


def pack(
    files: Iterable[str | Path] = (),
    output: str | Path = "archives",
    *,
    list_file: str | Path | None = None,
    root: str | Path | None = None,
    engine: str = "auto",
    seven_zip: str | Path | None = None,
    format: str | None = None,
    level: int | str = AUTO,
    volume_size: str | int = "10m",
    jobs: int = 0,
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
    force: bool = False,
    update: bool = False,
    verify: bool = False,
    manifest: str | Path | None = None,
    allow_archives: bool = False,
    strict: bool = False,
    dry_run: bool = False,
    password: str | None = None,
    retries: int = 3,
    sign_key: str | Path | None = None,
    sign_key_password: str | None = None,
    delete_source: bool = False,
    confirm: Confirm | None = None,
    progress: Progress | None = None,
) -> RunResult:
    """Archive files into split volumes and record them in a manifest.

    Each file gets its own folder ``<output>/<file name>/`` holding its volumes.
    Folders are kept: a file in a subfolder is stored under
    ``<output>/<folder>/<file name>/`` and ``unpack`` puts it back there. The
    folder is relative to the one ``list files`` recorded (so the listed folder's
    name is kept), else to the deepest folder all the files share; ``root``
    overrides this with a folder of your choice.

    Existing archives are skipped unless ``force`` is set; with ``update`` they
    are skipped only if the file and settings are unchanged, and re-archived
    otherwise. ``jobs`` is the total thread budget; ``progress`` is called from
    worker threads. With ``strict``, settings the engine cannot honour raise an
    error instead of a warning. With ``dry_run`` nothing is written.

    ``password`` encrypts the archives (the native engine needs
    ``batchvault[crypto]``). ``retries`` retries a file that failed on an I/O or
    engine error. ``sign_key`` signs the manifest with an Ed25519 private key.
    ``delete_source`` deletes each original after a full restore-and-compare
    proves the archive is good (and ``confirm``, if given, agrees).
    """
    if retries < 0:
        raise OptionsError("retries cannot be negative")
    level = parse_level(level)
    auto_level = level == AUTO  # each file gets the level that suits its type

    root_path = Path(root).expanduser().resolve() if root is not None else None
    sourced = gather_sources(
        files,
        list_file,
        path_filter=PathFilter(include, exclude),
        allow_archives=allow_archives,
        root=root_path,
    )
    sources = [path for path, _ in sourced]
    folders = dict(sourced)
    backend = get_engine(engine, seven_zip)
    warnings = option_warnings(backend, format, level)
    if strict and warnings:
        raise OptionsError(" ".join(warnings))
    if password and backend.name == "native":
        require_cryptography()

    private_key = None
    if sign_key and not dry_run:
        private_key = signing.load_private_key(Path(sign_key), sign_key_password)

    workers, per_file_jobs = _split_jobs(jobs, len(sources))
    options = PackOptions(
        level=5 if auto_level else level,
        volume_size=parse_size(volume_size),
        jobs=per_file_jobs,
        force=force,
        format=format or "xz",
        password=password,
    )

    out_root = Path(output).expanduser().resolve()
    manifest_path = (
        Path(manifest).expanduser().resolve() if manifest else out_root / MANIFEST_NAME
    )
    # Load first so a damaged manifest stops the run before any work is done.
    known = Manifest.load(manifest_path) if manifest_path.exists() else Manifest()
    if dry_run:
        return _plan_pack(
            backend, sources, folders, out_root, manifest_path.parent, options,
            warnings, known if update else None, auto_level,
        )  # fmt: skip

    cleanup_staging(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    log.info("Packing %d file(s) with %s into %s", len(sources), backend.name, out_root)
    total = sum(source.stat().st_size for source in sources)
    _notify(progress, ProgressEvent("plan", out_root, total, count=len(sources)))

    def work(source: Path) -> tuple[FileResult, ManifestEntry | None]:
        return _pack_one(
            backend, source, folders[source], out_root, manifest_path.parent,
            options, verify, progress, known if update else None, retries,
            auto_level,
        )  # fmt: skip

    with ThreadPoolExecutor(max_workers=workers) as executor:
        outcomes = list(executor.map(work, sources))

    result = RunResult(
        warnings=warnings, manifest=manifest_path, engine=backend.name
    )
    for file_result, entry in outcomes:
        result.entries.append(file_result)
        if entry:
            known.entries[entry.path] = entry

    if any(entry for _, entry in outcomes):
        known.save(manifest_path)
        _sign_or_clear(manifest_path, private_key, result)

    if delete_source:
        _delete_sources(backend, outcomes, password, confirm, result)
    return result


def _sign_or_clear(manifest_path: Path, private_key, result: RunResult) -> None:
    if private_key is not None:
        signing.sign_manifest(manifest_path, private_key)
        return

    stale = signing.signature_path(manifest_path)
    if stale.exists():
        stale.unlink()
        result.warnings.append(
            "The manifest changed, so its old signature was removed. "
            "Sign it again with a private key."
        )


def _file_options(
    options: PackOptions, source: Path, auto_level: bool
) -> tuple[PackOptions, LevelChoice | None]:
    """The options for one file; with ``--level auto`` the level suits its type."""
    if not auto_level or options.format == "store":  # store ignores the level
        return options, None
    choice = choose_level(source)
    return dataclasses.replace(options, level=choice.level), choice


def _folder_path(base: Path, folder: str) -> Path:
    """``base`` joined with a stored forward-slash folder (``base`` if empty)."""
    return base.joinpath(*folder.split("/")) if folder else base


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
    sizes = f"{format_size(size)} -> {format_size(stored)}"
    if size:
        sizes += f" ({stored / size:.0%})"
    parts = [f"{volumes} volume{'' if volumes == 1 else 's'}", sizes]
    if choice:
        parts.append(f"level {choice.level} ({choice.kind})")
    if verified:
        parts.append("verified")
    return ", ".join(parts)


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
) -> tuple[FileResult, ManifestEntry | None]:
    stat = source.stat()
    size = stat.st_size
    _notify(progress, ProgressEvent("start", source, size))
    options, choice = _file_options(options, source, auto_level)

    entry = None
    try:
        key = f"{folder}/{source.name}" if folder else source.name
        previous = known.entries.get(key) if known is not None else None
        if previous and _up_to_date(previous, stat, backend, options, manifest_dir):
            outcome = FileResult(source, Status.SKIPPED, "Up to date.", size)
        else:
            # In update mode a changed file replaces its old archive.
            file_options = options
            if known is not None:
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
    except OutputExistsError as exc:
        outcome = FileResult(source, Status.SKIPPED, str(exc), size)
    except (BatchVaultError, OSError) as exc:
        outcome, entry = FileResult(source, Status.FAILED, str(exc), size), None

    outcome.folder = folder
    outcome.name = source.name
    _log_result("pack", outcome)
    _notify(progress, ProgressEvent("finish", source, size, outcome.status))
    return outcome, entry


def _roundtrip_problem(
    backend: Engine, outcome: FileResult, entry: ManifestEntry, password: str | None
) -> str | None:
    """Restore the archive and compare it with the original; None if identical."""
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


def _delete_sources(
    backend: Engine,
    outcomes: list[tuple[FileResult, ManifestEntry | None]],
    password: str | None,
    confirm: Confirm | None,
    result: RunResult,
) -> None:
    verified = []
    for outcome, entry in outcomes:
        if outcome.status != Status.SUCCESS or entry is None:
            continue
        problem = _roundtrip_problem(backend, outcome, entry, password)
        if problem:
            outcome.warnings.append(f"Original kept: {problem}.")
            log.warning("Original kept for %s: %s", outcome.source, problem)
        else:
            verified.append(outcome)

    if not verified:
        return
    if confirm is not None and not confirm([o.source for o in verified]):
        result.warnings.append("Original files were kept (deletion not confirmed).")
        return

    for outcome in verified:
        try:
            outcome.source.unlink()
        except OSError as exc:
            outcome.warnings.append(f"Could not delete the original: {exc}")
        else:
            outcome.message += "; original deleted"
            log.info("Deleted original %s", outcome.source)


def _resolve(source: str | Path, public_key: str | Path | None) -> list[Target]:
    """Find the archives for ``source``.

    With ``public_key``, each manifest's signature is checked on the very bytes
    that are then parsed, so the file cannot be swapped in between.
    """
    if not public_key:
        return resolve_targets(source)

    key = Path(public_key)

    def load_signed(path: Path) -> Manifest:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ManifestError(f"Cannot read manifest {path}: {exc}") from exc
        signing.verify_manifest_bytes(raw, path, key)
        return Manifest.from_bytes(raw, path)

    targets = resolve_targets(source, load_signed)
    if any(target.manifest is None for target in targets):
        raise SignatureError("Signature check needs a manifest, but none was found.")
    return targets


def _plan_event(where: Path, targets: list[Target]) -> ProgressEvent:
    # Sizes are only known from a manifest; without one the UI counts files.
    known = all(target.entry for target in targets)
    total = sum(target.entry.size for target in targets if target.entry)
    return ProgressEvent("plan", where, total if known else 0, count=len(targets))


def unpack(
    source: str | Path,
    output: str | Path = "restored",
    *,
    force: bool = False,
    update: bool = False,
    jobs: int = 0,
    seven_zip: str | Path | None = None,
    password: str | None = None,
    public_key: str | Path | None = None,
    retries: int = 3,
    max_output_size: str | int | None = None,
    progress: Progress | None = None,
) -> RunResult:
    """Restore files from a manifest, a folder, any volume, or a list of volumes.

    With a manifest, volumes are checked first and the restored file is checked
    against its recorded SHA-256 before it is moved into place. Existing files
    are skipped unless ``force`` is set; with ``update`` they are skipped only
    when they already match the archive, and replaced otherwise. ``public_key``
    requires a valid manifest signature before anything is restored.

    ``max_output_size`` refuses an archive that would expand beyond that many
    bytes (a decompression bomb) before anything is written. With a manifest an
    archive may never expand beyond the size recorded for its file.
    """
    if retries < 0:
        raise OptionsError("retries cannot be negative")
    max_bytes = parse_size(max_output_size) if max_output_size is not None else None

    targets = _resolve(source, public_key)

    out_root = Path(output).expanduser().resolve()
    cleanup_staging(out_root)
    workers, _ = _split_jobs(jobs, len(targets))

    log.info("Unpacking %d archive(s) into %s", len(targets), out_root)
    _notify(progress, _plan_event(out_root, targets))

    def work(target: Target) -> FileResult:
        size = target.entry.size if target.entry else 0
        _notify(progress, ProgressEvent("start", target.volume, size))
        outcome = _restore_one(
            target, out_root, force, update, seven_zip, password, retries, max_bytes
        )
        outcome.folder = target.entry.folder if target.entry else ""
        outcome.name = target.name
        _log_result("unpack", outcome)
        _notify(progress, ProgressEvent("finish", target.volume, size, outcome.status))
        return outcome

    with ThreadPoolExecutor(max_workers=workers) as executor:
        outcomes = list(executor.map(work, targets))

    result = RunResult(entries=outcomes)
    if any(target.entry is None for target in targets):
        result.warnings.append(
            "No manifest found for some archives; their checksums were not verified."
        )
    return result


def _destination_folder(out_root: Path, folder: str) -> Path:
    """Where files stored under ``folder`` are restored, kept inside ``out_root``."""
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
) -> FileResult:
    volume = target.volume
    entry = target.entry
    try:
        limit = max_bytes
        if entry:  # the manifest says how big the file is; never accept more
            limit = entry.size if limit is None else min(limit, entry.size)

        folder = entry.folder if entry else ""
        destination_dir = _destination_folder(out_root, folder)
        destination = destination_dir / target.name
        if destination.exists() and not force:
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
            problems = entry.check_volumes(target.base_dir)
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

        size = sum(path.stat().st_size for path in final)
        checked = "checksum verified" if entry else "checksum not verified"
        message = f"{format_size(size)} restored, {checked}"
        return FileResult(volume, Status.SUCCESS, message, size, restored=final)
    except (BatchVaultError, OSError) as exc:
        return FileResult(volume, Status.FAILED, str(exc))


def _nfc(name: str) -> str:
    return unicodedata.normalize("NFC", name)


def _matches(path: Path, entry: ManifestEntry) -> bool:
    return path.is_file() and path.stat().st_size == entry.size and (
        hash_file(path) == entry.sha256
    )


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
    volume = target.volume
    entry = target.entry
    try:
        if entry:
            if entry.encrypted and not password:
                return FileResult(volume, Status.FAILED, ENCRYPTED_MESSAGE)
            problems = entry.check_volumes(target.base_dir)
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


def inspect(source: str | Path) -> list[InspectEntry]:
    """Describe archives from their manifest (or file names when there is none)."""
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

        paths = [target.base_dir / volume.file for volume in entry.volumes]
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
