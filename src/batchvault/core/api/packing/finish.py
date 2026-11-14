"""What happens after the files are packed: README, signature, source deletion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from batchvault.archive.manifest import Manifest, ManifestEntry
    from batchvault.core.api.packing.plan import Confirm
    from batchvault.core.models import FileResult, RunResult
    from batchvault.core.options import PackOptions
    from batchvault.engines.base import Engine

log = logging.getLogger(__name__)


def _write_readme(
    manifest_path: Path,
    manifest: Manifest,
    backend: Engine,
    options: PackOptions,
    auto_level: bool,
    include: tuple[str, ...],
    exclude: tuple[str, ...],
    update: bool,
    verified: bool,
    delete_source: bool,
    encrypted: bool,
    signed: bool,
    result: RunResult,
) -> None:
    from batchvault.archive.readme import (
        README_NAME,
        ReadmeContext,
        render_readme,
        write_readme,
    )
    from batchvault.core.api.packing.plan import _format_name

    context = ReadmeContext(
        engine=backend.name,
        format=_format_name(backend, options),
        level="auto" if auto_level else str(options.level),
        volume_size=options.volume_size,
        encrypted=encrypted,
        signed=signed,
        update=update,
        verified=verified,
        delete_source=delete_source,
        include=include,
        exclude=exclude,
        manifest_name=manifest_path.name,
    )
    warning = write_readme(
        manifest_path.with_name(README_NAME), render_readme(context, manifest)
    )
    if warning:
        result.warnings.append(warning)


def _sign_or_clear(manifest_path: Path, private_key, result: RunResult) -> None:
    from batchvault.archive import signing

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


def _delete_sources(
    backend: Engine,
    outcomes: list[tuple[FileResult, ManifestEntry | None]],
    password: str | None,
    confirm: Confirm | None,
    result: RunResult,
) -> None:
    from batchvault.core.api.packing.single import _roundtrip_problem
    from batchvault.core.models import Status

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
