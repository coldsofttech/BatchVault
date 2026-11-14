"""The ``inspect`` command."""

from __future__ import annotations


def cmd_inspect(args) -> int:
    import json

    from rich.console import Console

    from batchvault.cli import ui
    from batchvault.core import api

    entries = api.inspect(args.source)
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False))
    elif not args.quiet:
        ui.print_inspect(Console(), entries, args.source)
    return 0
