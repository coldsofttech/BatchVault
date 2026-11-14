"""Helpers shared by more than one operation of the public API."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from batchvault.core.models import ProgressEvent

if TYPE_CHECKING:
    from pathlib import Path

    from batchvault.core.locate import Target
    from batchvault.core.models import FileResult

log = logging.getLogger(__name__)


Progress = Callable[[ProgressEvent], None]


RETRY_BASE_DELAY = 1.0


RETRY_MAX_DELAY = 30.0


ENCRYPTED_MESSAGE = "Archive is encrypted; a password is required."


def _log_result(action: str, outcome: FileResult) -> None:
    from batchvault.core.models import Status

    level = logging.WARNING if outcome.status == Status.FAILED else logging.INFO
    log.log(
        level, "%s %s: %s %s", action, outcome.source, outcome.status, outcome.message
    )


def _split_jobs(jobs: int | None, files: int) -> tuple[int, int]:
    """Split one thread budget into (files in parallel, threads per file).

    A budget of 0 means no worker threads: one file at a time, compressed in
    the calling thread.
    """
    from batchvault.core.errors import InputError

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


def _retry(call, retries: int, retry_on: tuple[type[Exception], ...]):
    """Run ``call()``, retrying transient failures with exponential backoff."""
    import time

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


class _Stopped(Exception):
    """A queued file was not started because the run was stopped."""


def _run_all(workers: int, work: Callable, items: list) -> list:
    """Run ``work`` on every item in order; after an error or Ctrl+C, start no more.

    Files already running finish, so each is either complete or absent. The
    first error is raised.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    stop = threading.Event()

    def guarded(item):
        if stop.is_set():
            raise _Stopped
        try:
            return work(item)
        except BaseException:
            stop.set()
            raise

    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        results = list(executor.map(guarded, items))
    except BaseException:
        stop.set()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    executor.shutdown()
    return results


def _reject_resume_conflicts(function, given: dict) -> None:
    """Refuse settings passed together with ``resume`` (they come from the journal)."""
    import inspect as inspect_module
    from pathlib import Path

    from batchvault.core.errors import OptionsError

    parameters = inspect_module.signature(function).parameters
    clashing = []
    for name, value in given.items():
        default = parameters[name].default
        if value is None or isinstance(value, bool | int | str | Path):
            differs = value != default
        else:
            differs = list(value) != list(default)
        if differs:
            clashing.append(name)
    if clashing:
        raise OptionsError(
            "resume takes every setting from the earlier run, so do not also pass: "
            + ", ".join(clashing)
            + "."
        )


def _folder_path(base: Path, folder: str) -> Path:
    """``base`` joined with a stored forward-slash folder (``base`` if empty)."""
    return base.joinpath(*folder.split("/")) if folder else base


def _resolve(source: str | Path, public_key: str | Path | None) -> list[Target]:
    """Find the archives for ``source``.

    With ``public_key``, each manifest's signature is checked on the very bytes
    that are then parsed, so the file cannot be swapped in between.
    """
    from pathlib import Path

    from batchvault.archive import signing
    from batchvault.archive.manifest import Manifest
    from batchvault.core.errors import ManifestError, SignatureError
    from batchvault.core.locate import resolve_targets

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
