"""The ``unpack`` command."""

from __future__ import annotations

UNPACK_RESUME_FIXED: dict[str, tuple[str, ...]] = {
    "force": ("--force",),
    "update": ("--update",),
}


def cmd_unpack(args) -> int:
    from pathlib import Path

    from batchvault.cli import ui
    from batchvault.cli._common import (
        _check_output_flags,
        _consoles,
        _finish,
        _notify_url,
        _progress,
        _read_password,
        _reject_given,
    )
    from batchvault.core import api
    from batchvault.core.errors import InputError

    _check_output_flags(args)
    if args.resume:
        _reject_given(args, UNPACK_RESUME_FIXED, ["SOURCE"] if args.source else [])
    elif not args.source:
        raise InputError("Give SOURCE, or use --resume to continue an earlier run.")
    password = _read_password(args)
    url = _notify_url(args)

    out, err = _consoles()
    if not (args.quiet or args.json or args.json_events):
        ui.print_run_info(
            out,
            "BatchVault unpack",
            [
                (
                    "Archives",
                    "from the earlier run in the output folder (resuming)"
                    if args.resume
                    else args.source,
                ),
                ("Restore files to", str(Path(args.output).resolve())),
                ("Existing files", "overwrite" if args.force else "skip"),
                ("Jobs", str(args.jobs) if args.jobs else "0 (sequential)"),
            ],
        )
    with _progress(args, "Restoring", err) as progress:
        result = api.unpack(
            args.source,
            args.output,
            force=args.force,
            update=args.update,
            jobs=args.jobs,
            seven_zip=args.seven_zip,
            password=password,
            public_key=args.public_key,
            retries=args.retries,
            max_output_size=args.max_output_size,
            resume=args.resume,
            progress=progress,
        )
    return _finish(args, "unpack", result, None, progress, url)
