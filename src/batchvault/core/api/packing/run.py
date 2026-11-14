"""The ``pack`` operation: plan the run, pack each file, write the manifest."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from batchvault.core.levels import AUTO

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from batchvault.archive.manifest import ManifestEntry
    from batchvault.core.api._common import Progress
    from batchvault.core.api.packing.plan import Confirm
    from batchvault.core.models import FileResult, RunResult

log = logging.getLogger(__name__)


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
    readme: bool = True,
    resume: bool = False,
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

    Unless ``readme`` is false, a ``BATCHVAULT-README.md`` is written next to the
    manifest: how the archive was made, how to install BatchVault and how to
    restore it. It never holds a password or key. An existing file of that name
    is only replaced if BatchVault wrote it.

    While it runs, ``pack`` keeps a journal (``.batchvault-pack.jsonl``) in the
    output folder, recording every finished file; it is removed when the run ends
    without failures. With ``resume`` the run continues from that journal: every
    setting (files, engine, format, level, volume size, patterns, ...) is taken
    from the earlier run, so passing one of them as well is an error, and so is
    having no journal. Files that are finished, unchanged and whose volumes still
    match their checksums are reused; the rest are done again. ``password`` and
    ``sign_key`` are never stored, so give them again if the earlier run used
    them. ``jobs``, ``retries``, ``seven_zip``, ``confirm`` and ``progress`` may
    be passed freely.
    """
    from pathlib import Path

    from batchvault.archive import resume as journals
    from batchvault.archive import signing
    from batchvault.archive.crypto import require_cryptography
    from batchvault.archive.manifest import MANIFEST_NAME, Manifest
    from batchvault.archive.volumes import cleanup_staging
    from batchvault.core.api._common import (
        _notify,
        _reject_resume_conflicts,
        _run_all,
        _split_jobs,
    )
    from batchvault.core.api.packing.finish import (
        _delete_sources,
        _sign_or_clear,
        _write_readme,
    )
    from batchvault.core.api.packing.plan import _check_resume_password, _plan_pack
    from batchvault.core.api.packing.single import _pack_one
    from batchvault.core.errors import OptionsError, ResumeError
    from batchvault.core.filters import PathFilter
    from batchvault.core.inputs import gather_sources
    from batchvault.core.levels import parse_level
    from batchvault.core.models import FileResult, ProgressEvent, RunResult, Status
    from batchvault.core.options import PackOptions, parse_size
    from batchvault.engines.registry import get_engine, option_warnings

    if retries < 0:
        raise OptionsError("retries cannot be negative")
    files, include, exclude = list(files), tuple(include), tuple(exclude)
    out_root = Path(output).expanduser().resolve()

    saved = None
    journaled: dict[str, ManifestEntry] = {}
    loaded = None
    if resume:
        _reject_resume_conflicts(
            pack,
            dict(
                files=files, list_file=list_file, root=root, engine=engine,
                format=format, level=level, volume_size=volume_size,
                include=include, exclude=exclude, force=force, update=update,
                verify=verify, manifest=manifest, allow_archives=allow_archives,
                strict=strict, dry_run=dry_run, delete_source=delete_source,
                readme=readme,
            ),
        )  # fmt: skip
        saved, journaled, loaded = journals.load_pack(out_root / journals.PACK_JOURNAL)
        engine, format, level = saved.engine, saved.format, saved.level
        volume_size, include, exclude = (
            saved.volume_size,
            tuple(saved.include),
            tuple(saved.exclude),
        )
        manifest, update, verify = saved.manifest, saved.update, saved.verify
        delete_source, strict, readme = saved.delete_source, saved.strict, saved.readme
        if saved.encrypted and not password:
            raise ResumeError(
                "The earlier run was encrypted, so the password is required to "
                "resume (it is never stored)."
            )
        if password and not saved.encrypted:
            raise ResumeError(
                "The earlier run was not encrypted; do not pass a password, or "
                "the archive would mix encrypted and plain files."
            )
        if saved.signed and not sign_key:
            raise ResumeError(
                "The earlier run signed the manifest, so the private key is "
                "required to sign it again (it is never stored)."
            )
        if sign_key and not saved.signed:
            raise ResumeError(
                "The earlier run did not sign the manifest; do not pass a private key."
            )

    level = parse_level(level)
    auto_level = level == AUTO  # each file gets the level that suits its type

    if saved is not None:
        sourced = [(Path(path), folder) for path, folder in saved.sources]
    else:
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
        level=level if isinstance(level, int) else 5,
        volume_size=parse_size(volume_size),
        jobs=per_file_jobs,
        force=force,
        format=format or "xz",
        password=password,
    )

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

    journal_path = out_root / journals.PACK_JOURNAL
    missing: list[tuple[FileResult, ManifestEntry | None]] = []
    if resume and loaded is not None:
        if password:
            _check_resume_password(backend, journaled, manifest_path.parent, password)
        present = []
        for source in sources:  # a source that vanished cannot be archived again
            if source.is_file():
                present.append(source)
                continue
            failure = FileResult(source, Status.FAILED, "Source file not found.")
            failure.folder, failure.name = folders[source], source.name
            missing.append((failure, None))
        sources = present
        journal = journals.Journal.reopen(journal_path, loaded.good_size)
    else:
        journal = journals.Journal.create(
            journal_path,
            "pack",
            journals.PackSettings(
                sources=[(str(path), folder) for path, folder in sourced],
                engine=backend.name, format=format, level=level,
                volume_size=options.volume_size, jobs=jobs, retries=retries,
                include=list(include), exclude=list(exclude),
                manifest=str(manifest_path), update=update, verify=verify,
                delete_source=delete_source, strict=strict, readme=readme,
                encrypted=bool(password), signed=bool(sign_key),
            ).to_dict(),
        )  # fmt: skip

    log.info(
        "%s %d file(s) with %s into %s",
        "Resuming" if resume else "Packing",
        len(sources),
        backend.name,
        out_root,
    )
    total = sum(source.stat().st_size for source in sources)
    _notify(progress, ProgressEvent("plan", out_root, total, count=len(sources)))

    def work(source: Path) -> tuple[FileResult, ManifestEntry | None]:
        folder = folders[source]
        key = f"{folder}/{source.name}" if folder else source.name
        outcome, entry = _pack_one(
            backend, source, folder, out_root, manifest_path.parent,
            options, verify, progress, known if update else None, retries,
            auto_level, journaled.get(key), resume,
        )  # fmt: skip
        if entry is not None and not outcome.resumed:
            journal.append({"kind": "file", "key": key, "entry": entry.to_dict()})
        return outcome, entry

    try:
        outcomes = _run_all(workers, work, sources)
    except BaseException:
        journal.close()  # keep it: it is what --resume continues from
        raise
    outcomes += missing

    result = RunResult(
        warnings=warnings, manifest=manifest_path, engine=backend.name, resume=resume
    )
    for file_result, entry in outcomes:
        result.entries.append(file_result)
        if entry:
            known.entries[entry.path] = entry

    if any(entry for _, entry in outcomes):
        known.save(manifest_path)
        _sign_or_clear(manifest_path, private_key, result)
        if readme:
            _write_readme(
                manifest_path, known, backend, options, auto_level, include, exclude,
                update, verify, delete_source, bool(password), private_key is not None,
                result,
            )  # fmt: skip

    if delete_source:
        _delete_sources(backend, outcomes, password, confirm, result)

    # Keep the journal while something failed so --resume can retry just that.
    if any(file_result.status == Status.FAILED for file_result, _ in outcomes):
        journal.close()
    else:
        journal.remove()
    return result
