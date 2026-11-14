"""The ``unpack`` operation: plan the run and restore each file."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from batchvault.core.api._common import Progress
    from batchvault.core.models import RunResult

log = logging.getLogger(__name__)


def unpack(
    source: str | Path | None = None,
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
    resume: bool = False,
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

    While it runs, ``unpack`` keeps a journal (``.batchvault-unpack.jsonl``) in
    the output folder, recording the files it placed; it is removed when the run
    ends without failures. With ``resume`` (and no ``source``) the run continues
    from that journal, using the source of the earlier run: a file that is
    already there and matches its checksum is kept, a missing one is restored,
    and one this run wrote earlier but that no longer matches is restored again.
    A file that differs and was not written by this run is never touched.
    ``force`` and ``update`` cannot be combined with ``resume``; ``public_key``
    must be given again if the earlier run required a signature.
    """
    from pathlib import Path

    from batchvault.archive import resume as journals
    from batchvault.archive.manifest import hash_file
    from batchvault.archive.volumes import cleanup_staging
    from batchvault.core.api._common import (
        _log_result,
        _notify,
        _plan_event,
        _reject_resume_conflicts,
        _resolve,
        _run_all,
        _split_jobs,
    )
    from batchvault.core.api.unpacking.restore import _restore_one
    from batchvault.core.errors import OptionsError, ResumeError
    from batchvault.core.locate import Target
    from batchvault.core.models import FileResult, ProgressEvent, RunResult, Status
    from batchvault.core.options import parse_size

    if retries < 0:
        raise OptionsError("retries cannot be negative")
    max_bytes = parse_size(max_output_size) if max_output_size is not None else None
    out_root = Path(output).expanduser().resolve()

    saved = None
    placed: dict[str, str] = {}
    loaded = None
    if resume:
        _reject_resume_conflicts(
            unpack, dict(source=source, force=force, update=update)
        )
        saved, placed, loaded = journals.load_unpack(out_root / journals.UNPACK_JOURNAL)
        if saved.signed and not public_key:
            raise ResumeError(
                "The earlier run required a valid signature, so the public key "
                "is required to resume."
            )
        source = saved.source
    elif source is None:
        raise OptionsError("source is required unless resume is set.")

    targets = _resolve(source, public_key)

    manifests = {
        str(path): hash_file(path)
        for path in {target.manifest for target in targets if target.manifest}
    }
    if resume and saved is not None:
        if any(target.entry is None for target in targets):
            raise ResumeError("Resuming needs a manifest to check the files against.")
        if manifests != saved.manifests:
            raise ResumeError(
                "The archive's manifest is not the one the earlier run used "
                "(it was replaced or changed), so the output cannot be resumed. "
                "Run unpack without --resume to start again."
            )

    cleanup_staging(out_root)
    workers, _ = _split_jobs(jobs, len(targets))

    journal = None
    created_folder = not out_root.exists()
    if manifests:
        out_root.mkdir(parents=True, exist_ok=True)
        journal_path = out_root / journals.UNPACK_JOURNAL
        if resume and loaded is not None:
            journal = journals.Journal.reopen(journal_path, loaded.good_size)
        else:
            journal = journals.Journal.create(
                journal_path,
                "unpack",
                journals.UnpackSettings(
                    source=str(Path(str(source)).expanduser().resolve()),
                    manifests=manifests,
                    signed=bool(public_key),
                ).to_dict(),
            )

    log.info(
        "%s %d archive(s) into %s",
        "Resuming" if resume else "Unpacking",
        len(targets),
        out_root,
    )
    _notify(progress, _plan_event(out_root, targets))

    def work(target: Target) -> FileResult:
        size = target.entry.size if target.entry else 0
        _notify(progress, ProgressEvent("start", target.volume, size))
        outcome = _restore_one(
            target, out_root, force, update, seven_zip, password, retries, max_bytes,
            resume, placed, journal,
        )  # fmt: skip
        outcome.folder = target.entry.folder if target.entry else ""
        outcome.name = target.name
        _log_result("unpack", outcome)
        _notify(
            progress,
            ProgressEvent(
                "finish", target.volume, size, outcome.status, resumed=outcome.resumed
            ),
        )
        return outcome

    try:
        outcomes = _run_all(workers, work, targets)
    except BaseException:
        if journal:
            journal.close()  # keep it: it is what --resume continues from
        raise

    # Keep the journal while something failed so --resume can retry just that.
    if journal:
        failed = any(outcome.status == Status.FAILED for outcome in outcomes)
        if failed and (journal.appended or resume):
            journal.close()
        else:  # finished, or failed before restoring anything: nothing to resume
            journal.remove()
            if created_folder:
                try:
                    out_root.rmdir()  # only if it is empty
                except OSError:
                    pass

    result = RunResult(entries=outcomes, resume=resume)
    if any(target.entry is None for target in targets):
        result.warnings.append(
            "No manifest found for some archives; their checksums were not verified."
        )
    return result
