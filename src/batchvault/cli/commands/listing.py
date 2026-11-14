"""The ``list files`` and ``list archives`` commands."""

from __future__ import annotations


def _write_listing(args, entries, noun: str) -> None:
    import json
    from pathlib import Path

    from rich.markup import escape

    from batchvault.cli._common import _consoles
    from batchvault.core.inputs import ROOT_HEADER

    _, err = _consoles()
    if not args.output:
        print(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False))
        return

    path = Path(args.output).expanduser()
    if path.suffix.lower() == ".json":
        text = json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False)
        text += "\n"
    else:
        text = "".join(f"{entry.path}\n" for entry in entries)
        roots = {entry.root for entry in entries if entry.root}
        if len(roots) == 1:
            # Remembered by ``pack`` so the listed folder's name is kept.
            text = f"{ROOT_HEADER} {roots.pop()}\n" + text
    path.write_text(text, encoding="utf-8")

    if not args.quiet:
        err.print(f"Found {len(entries)} {noun}(s). List saved to: {escape(str(path))}")


def cmd_list_files(args) -> int:
    from batchvault.cli._common import _consoles
    from batchvault.core import api
    from batchvault.core.inputs import looks_like_archive

    _, err = _consoles()
    entries = api.list_files(args.folder, include=args.include, exclude=args.exclude)

    if any(looks_like_archive(entry.path) for entry in entries) and not args.quiet:
        err.print(
            "[yellow]Warning: this folder contains archive volumes or a manifest. "
            "Did you mean `batchvault list archives`?[/yellow]"
        )
    _write_listing(args, entries, "file")
    return 0


def cmd_list_archives(args) -> int:
    from batchvault.cli._common import _consoles
    from batchvault.core import api

    _, err = _consoles()
    entries = api.list_archives(args.folder)

    if not entries and not args.quiet:
        err.print(
            "[yellow]Warning: no archives found. "
            "Did you mean `batchvault list files`?[/yellow]"
        )
    _write_listing(args, entries, "archive")
    return 0
