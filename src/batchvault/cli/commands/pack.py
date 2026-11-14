"""The ``pack`` command."""

from __future__ import annotations


def _confirm_deletion(args, progress):
    import sys
    from pathlib import Path

    def confirm(paths: list[Path]) -> bool:
        if args.yes:
            return True
        quiet = args.json or args.json_events or args.quiet
        if quiet or not sys.stdin.isatty():
            return False  # never delete without an explicit yes

        if hasattr(progress, "pause"):
            progress.pause()
        print(f"{len(paths)} original file(s) were archived and verified:")
        for path in paths[:10]:
            print(f"  {path}")
        if len(paths) > 10:
            print(f"  ... and {len(paths) - 10} more")
        answer = input("Delete them? [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    return confirm


def _describe_level(level) -> str:
    from batchvault.core.levels import AUTO

    return "auto (chosen for each file's type)" if level == AUTO else str(level)


def _describe_sources(args) -> str:
    parts = []
    if args.files:
        parts.append(f"{len(args.files)} given")
    if args.list:
        parts.append("list: " + ("standard input" if args.list == "-" else args.list))
    return ", ".join(parts) or "-"


# Options whose values come from the earlier run when resuming. Passing any of
# them with --resume is refused rather than ignored or allowed to mix outputs.
PACK_RESUME_FIXED: dict[str, tuple[str, ...]] = {
    "list": ("--list",),
    "engine": ("--engine",),
    "format": ("--format",),
    "level": ("--level",),
    "volume_size": ("--volume-size",),
    "include": ("--include",),
    "exclude": ("--exclude",),
    "root": ("--root",),
    "manifest": ("--manifest",),
    "force": ("--force",),
    "update": ("--update",),
    "verify": ("--verify",),
    "encrypt": ("--encrypt",),
    "delete_source": ("--delete-source",),
    "allow_archives": ("--allow-archives",),
    "strict": ("--strict",),
    "dry_run": ("--dry-run",),
    "readme": ("--no-readme",),
}


def cmd_pack(args) -> int:
    import os
    from pathlib import Path

    from batchvault.archive import resume as journals
    from batchvault.cli import ui
    from batchvault.cli._common import (
        ENV_KEY_PASSWORD,
        ENV_PASSWORD,
        _check_output_flags,
        _consoles,
        _finish,
        _given,
        _notify_url,
        _progress,
        _read_password,
        _reject_given,
    )
    from batchvault.core import api
    from batchvault.core.errors import InputError
    from batchvault.core.options import format_size

    _check_output_flags(args)

    saved = None
    if args.resume:
        _reject_given(args, PACK_RESUME_FIXED, ["FILE"] if args.files else [])
        saved, _, _ = journals.load_pack(
            Path(args.output).expanduser().resolve() / journals.PACK_JOURNAL
        )
    elif not args.files and not args.list:
        raise InputError("Give at least one FILE or use --list.")

    jobs, retries = args.jobs, args.retries
    if saved is not None:  # the earlier run's values unless given again
        jobs = jobs if _given(args, "jobs", ("--jobs", "-j")) else saved.jobs
        retries = retries if _given(args, "retries", ("--retries",)) else saved.retries

    password = None
    if args.encrypt or (saved is not None and saved.encrypted):
        password = _read_password(args)
        if not password:
            raise InputError(
                ("The earlier run was encrypted: " if saved else "--encrypt: ")
                + f"set {ENV_PASSWORD} or use --password-file."
            )
    url = _notify_url(args)

    out, err = _consoles()
    if not (args.quiet or args.json or args.json_events):
        if saved is not None:
            rows = [
                ("Resuming", "the earlier run in this folder (its settings are kept)"),
                ("Engine", saved.engine),
                ("Format", saved.format or "xz (native engine)"),
                ("Level", _describe_level(saved.level)),
                ("Volume size", format_size(saved.volume_size)),
                ("Jobs", str(jobs) if jobs else "0 (sequential)"),
                ("Source files", f"{len(saved.sources)} from the earlier run"),
                ("Archives folder", str(Path(args.output).resolve())),
            ]
        else:
            rows = [
                ("Engine", args.engine),
                ("Format", args.format or "xz (native engine)"),
                ("Level", _describe_level(args.level)),
                ("Volume size", args.volume_size),
                ("Jobs", str(args.jobs) if args.jobs else "0 (sequential)"),
                ("Source files", _describe_sources(args)),
                (
                    "Folders",
                    f"kept, relative to {Path(args.root).resolve()}"
                    if args.root
                    else "kept",
                ),
                ("Archives folder", str(Path(args.output).resolve())),
            ]
        ui.print_run_info(out, "BatchVault pack", rows)

    if args.resume:
        with _progress(args, "Archiving", err) as progress:
            result = api.pack(
                output=args.output,
                resume=True,
                seven_zip=args.seven_zip,
                jobs=jobs,
                retries=retries,
                password=password,
                sign_key=args.sign_key,
                sign_key_password=os.environ.get(ENV_KEY_PASSWORD) or None,
                confirm=_confirm_deletion(args, progress),
                progress=progress,
            )
        return _finish(args, "pack", result, None, progress, url)

    with _progress(args, "Archiving", err) as progress:
        result = api.pack(
            args.files,
            args.output,
            list_file=args.list,
            root=args.root,
            engine=args.engine,
            seven_zip=args.seven_zip,
            format=args.format,
            level=args.level,
            volume_size=args.volume_size,
            jobs=args.jobs,
            include=args.include,
            exclude=args.exclude,
            force=args.force,
            update=args.update,
            verify=args.verify,
            manifest=args.manifest,
            allow_archives=args.allow_archives,
            strict=args.strict,
            dry_run=args.dry_run,
            password=password,
            retries=args.retries,
            sign_key=args.sign_key,
            sign_key_password=os.environ.get(ENV_KEY_PASSWORD) or None,
            delete_source=args.delete_source and not args.dry_run,
            readme=args.readme,
            confirm=_confirm_deletion(args, progress),
            progress=progress,
        )

    title = None  # the default title names the source files and archives
    if args.dry_run:
        title = "Pack plan (dry run): source files -> archives"
        if not (args.quiet or args.json or args.json_events):
            out.print("[bold]Dry run:[/bold] nothing was written.")
    return _finish(args, "pack", result, title, progress, url)
