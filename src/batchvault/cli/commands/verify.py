"""The ``verify`` command."""

from __future__ import annotations


def cmd_verify(args) -> int:
    from batchvault.cli import ui
    from batchvault.cli._common import (
        _check_output_flags,
        _consoles,
        _finish,
        _notify_url,
        _progress,
        _read_password,
    )
    from batchvault.core import api

    _check_output_flags(args)
    password = _read_password(args)
    url = _notify_url(args)

    out, err = _consoles()
    if not (args.quiet or args.json or args.json_events):
        ui.print_run_info(
            out,
            "BatchVault verify",
            [
                ("Archives", args.source),
                ("Original files", args.against or "not compared"),
                ("Jobs", str(args.jobs) if args.jobs else "0 (sequential)"),
            ],
        )
    with _progress(args, "Verifying", err) as progress:
        result = api.verify(
            args.source,
            against=args.against,
            jobs=args.jobs,
            seven_zip=args.seven_zip,
            password=password,
            public_key=args.public_key,
            progress=progress,
        )
    return _finish(args, "verify", result, None, progress, url)
