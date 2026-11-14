"""The ``doctor`` command."""

from __future__ import annotations


def cmd_doctor(args) -> int:
    import json

    from rich.console import Console

    from batchvault.cli import ui
    from batchvault.cli.doctor import passed, run_checks

    checks = run_checks(args.seven_zip)
    if args.json:
        print(
            json.dumps([vars(check) for check in checks], indent=2, ensure_ascii=False)
        )
    elif not args.quiet:
        ui.print_checks(Console(), checks)
    return 0 if passed(checks) else 1
