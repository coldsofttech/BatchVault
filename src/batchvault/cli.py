"""Command-line interface: a thin wrapper over :mod:`batchvault.api`.

Exit codes: 0 success, 1 some files failed, were skipped or the checks failed,
2 usage or configuration error, 3 unexpected error, 130 interrupted.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from batchvault import __version__, api, notify, signing
from batchvault.config import (
    defaults_for,
    find_config,
    load_config,
    reject_restricted,
)
from batchvault.doctor import passed, run_checks
from batchvault.engines.registry import ENGINE_CHOICES
from batchvault.errors import BatchVaultError, ConfigError, InputError
from batchvault.inputs import ROOT_HEADER, looks_like_archive
from batchvault.levels import AUTO, level_argument
from batchvault.logs import LOG_LEVELS, close_logging, setup_logging
from batchvault.manifest import Manifest
from batchvault.models import RunResult, Status
from batchvault.options import FORMATS
from batchvault.security import redact, redact_arguments

log = logging.getLogger(__name__)

ENV_PASSWORD = "BATCHVAULT_PASSWORD"
ENV_KEY_PASSWORD = "BATCHVAULT_KEY_PASSWORD"
ENV_NOTIFY_URL = "BATCHVAULT_NOTIFY_URL"
ENV_CONFIG = "BATCHVAULT_CONFIG"


def _parents() -> dict[str, argparse.ArgumentParser]:
    parents = {name: argparse.ArgumentParser(add_help=False) for name in (
        "common", "jobs", "seven", "patterns", "run", "retry", "public",
    )}  # fmt: skip

    common = parents["common"]
    common.add_argument(
        "--json", action="store_true", help="Print the result as JSON on stdout."
    )
    common.add_argument(
        "-q", "--quiet", action="store_true", help="No progress or summary output."
    )
    common.add_argument("--log-file", help="Write the log here instead of the default.")
    common.add_argument(
        "--log-level", choices=LOG_LEVELS, default="INFO", help="Log level."
    )
    common.add_argument(
        "--no-log", action="store_true", help="Do not write a log file."
    )
    common.add_argument(
        "--config", metavar="FILE", help="Config file (default: batchvault.toml)."
    )

    parents["jobs"].add_argument(
        "-j",
        "--jobs",
        type=int,
        default=0,
        metavar="N",
        help="Worker threads to use; 0 means none, one file at a time (default: 0).",
    )

    parents["seven"].add_argument(
        "--7z", dest="seven_zip", metavar="PATH", help="Path to the 7-Zip executable."
    )

    patterns = parents["patterns"]
    patterns.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="Only keep matching paths (repeatable; @FILE reads patterns from a file).",
    )
    patterns.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Drop matching paths (repeatable; wins over --include).",
    )

    run = parents["run"]
    run.add_argument(
        "--json-events",
        action="store_true",
        help="Print one JSON event per line on stdout while running.",
    )
    run.add_argument(
        "--notify",
        metavar="URL",
        help=f"POST a JSON summary to this webhook (or set {ENV_NOTIFY_URL}).",
    )
    run.add_argument(
        "--notify-on",
        choices=notify.NOTIFY_ON,
        default="failure",
        help="When to notify (default: failure).",
    )
    run.add_argument(
        "--password-file",
        metavar="FILE",
        help=f"Read the archive password from this file (or set {ENV_PASSWORD}).",
    )

    parents["retry"].add_argument(
        "--retries",
        type=int,
        default=3,
        metavar="N",
        help="Retry a file this many times after an I/O or engine error (default: 3).",
    )

    parents["public"].add_argument(
        "--public-key",
        metavar="FILE",
        help="Require a valid manifest signature made with the matching private key.",
    )
    return parents


def _configure(subparser, name: str, config: dict | None) -> None:
    """Apply defaults from the config file to one command."""
    if not config:
        return
    actions = {action.dest: action for action in subparser._actions}
    defaults = defaults_for(config, name, actions)
    if defaults:
        subparser.set_defaults(**defaults)


def build_parser(config: dict | None = None) -> argparse.ArgumentParser:
    p = _parents()
    common, jobs, seven, patterns, run, retry, public = (
        p["common"], p["jobs"], p["seven"], p["patterns"], p["run"], p["retry"],
        p["public"],
    )  # fmt: skip

    parser = argparse.ArgumentParser(
        prog="batchvault",
        description="Batch archive, compress, split and restore files.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    pack = commands.add_parser(
        "pack",
        parents=[common, jobs, seven, patterns, run, retry],
        help="Compress files into split volumes.",
        description="Compress files into split volumes and write a manifest.",
        epilog="Example: batchvault pack --list archive_list.txt -o archives",
    )
    pack.add_argument("files", nargs="*", metavar="FILE", help="Files to archive.")
    pack.add_argument(
        "--list",
        metavar="FILE",
        help="Text or JSON list of files to archive ('-' reads standard input).",
    )
    pack.add_argument("-o", "--output", default="archives", help="Output folder.")
    pack.add_argument("--engine", choices=ENGINE_CHOICES, default="auto")
    pack.add_argument(
        "--format",
        choices=FORMATS,
        help="Native engine compression (default: xz). 7-Zip always writes 7z.",
    )
    pack.add_argument(
        "--level",
        type=level_argument,
        choices=[*range(10), AUTO],
        default=AUTO,
        metavar="0-9|auto",
        help="Compression level, or auto to pick one per file type (default: auto).",
    )
    pack.add_argument(
        "--volume-size",
        default="10m",
        metavar="SIZE",
        help="Volume size, e.g. 500k, 10m, 2g (default: 10m).",
    )
    pack.add_argument("--manifest", metavar="PATH", help="Manifest file location.")
    pack.add_argument(
        "--root",
        metavar="DIR",
        help="Advanced: store paths relative to DIR. By default folders are kept "
        "automatically (relative to the folder you listed, or the folder your "
        "files share).",
    )
    pack.add_argument("--force", action="store_true", help="Replace existing archives.")
    pack.add_argument(
        "--update",
        action="store_true",
        help="Skip unchanged files and re-archive changed ones.",
    )
    pack.add_argument(
        "--verify", action="store_true", help="Test each archive after creating it."
    )
    pack.add_argument(
        "--encrypt",
        action="store_true",
        help=f"Encrypt with the password from {ENV_PASSWORD} or --password-file.",
    )
    pack.add_argument(
        "--sign-key",
        metavar="FILE",
        help=f"Sign the manifest with this private key (password: {ENV_KEY_PASSWORD}).",
    )
    pack.add_argument(
        "--delete-source",
        action="store_true",
        help="Delete each original after proving the archive restores identically.",
    )
    pack.add_argument(
        "--yes", action="store_true", help="Do not ask before --delete-source deletes."
    )
    pack.add_argument(
        "--allow-archives",
        action="store_true",
        help="Allow archiving BatchVault/7-Zip volumes and manifests.",
    )
    pack.add_argument(
        "--strict",
        action="store_true",
        help="Treat settings the engine cannot honour as errors.",
    )
    pack.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be archived without writing anything.",
    )
    pack.set_defaults(handler=cmd_pack)
    _configure(pack, "pack", config)

    unpack = commands.add_parser(
        "unpack",
        parents=[common, jobs, seven, run, retry, public],
        help="Restore files from archives.",
        description="Restore files from a manifest, folder, volume or list file.",
        epilog="Example: batchvault unpack archives -o restored",
    )
    unpack.add_argument(
        "source", metavar="SOURCE", help="Manifest, folder, volume or list file."
    )
    unpack.add_argument("-o", "--output", default="restored", help="Output folder.")
    unpack.add_argument(
        "--force", action="store_true", help="Overwrite existing files."
    )
    unpack.add_argument(
        "--update",
        action="store_true",
        help="Skip files that already match; replace the rest.",
    )
    unpack.add_argument(
        "--max-output-size",
        metavar="SIZE",
        help="Refuse an archive that would expand beyond this size, per file "
        "(e.g. 50g). Use it when restoring archives you do not trust.",
    )
    unpack.set_defaults(handler=cmd_unpack)
    _configure(unpack, "unpack", config)

    verify = commands.add_parser(
        "verify",
        parents=[common, jobs, seven, run, public],
        help="Check that archives are complete and intact.",
        description="Check archives without restoring them.",
    )
    verify.add_argument(
        "source", metavar="SOURCE", help="Manifest, folder, volume or list file."
    )
    verify.add_argument(
        "--against",
        metavar="FOLDER",
        help="Also compare with the original files in this folder (and below).",
    )
    verify.set_defaults(handler=cmd_verify)
    _configure(verify, "verify", config)

    inspect = commands.add_parser(
        "inspect",
        parents=[common],
        help="Show what is in an archive set.",
        description="Show sizes, ratios, engine, format and completeness.",
    )
    inspect.add_argument(
        "source", metavar="SOURCE", help="Manifest, folder, volume or list file."
    )
    inspect.set_defaults(handler=cmd_inspect)
    _configure(inspect, "inspect", config)

    listing = commands.add_parser("list", help="List files or archives.")
    kinds = listing.add_subparsers(dest="kind", required=True, metavar="KIND")

    files = kinds.add_parser(
        "files",
        parents=[common, patterns],
        help="List the files in a folder.",
        description="List files (JSON on stdout unless -o is given).",
    )
    files.add_argument("folder")
    files.add_argument("-o", "--output", metavar="FILE", help="Write .json or .txt.")
    files.set_defaults(handler=cmd_list_files)
    _configure(files, "list-files", config)

    archives = kinds.add_parser(
        "archives",
        parents=[common],
        help="List the archives in a folder.",
        description="List archives (JSON on stdout unless -o is given).",
    )
    archives.add_argument("folder")
    archives.add_argument("-o", "--output", metavar="FILE", help="Write .json or .txt.")
    archives.set_defaults(handler=cmd_list_archives)
    _configure(archives, "list-archives", config)

    keygen = commands.add_parser(
        "keygen",
        parents=[common],
        help="Create a key pair for signing manifests.",
        description=(
            "Create an Ed25519 key pair (needs batchvault[crypto]). The private "
            f"key is encrypted if {ENV_KEY_PASSWORD} is set."
        ),
    )
    keygen.add_argument("--private", default="batchvault.key", metavar="FILE")
    keygen.add_argument("--public", default="batchvault.pub", metavar="FILE")
    keygen.add_argument("--force", action="store_true", help="Replace existing keys.")
    keygen.set_defaults(handler=cmd_keygen)
    _configure(keygen, "keygen", config)

    sign = commands.add_parser(
        "sign",
        parents=[common],
        help="Sign a manifest.",
        description="Write a detached signature next to a manifest.",
    )
    sign.add_argument("manifest", metavar="MANIFEST")
    sign.add_argument("--key", required=True, metavar="FILE", help="Private key.")
    sign.set_defaults(handler=cmd_sign)
    _configure(sign, "sign", config)

    doctor = commands.add_parser(
        "doctor",
        parents=[common, seven],
        help="Check this machine's setup.",
        description="Check Python modules, 7-Zip and the log folder.",
    )
    doctor.set_defaults(handler=cmd_doctor)
    _configure(doctor, "doctor", config)

    return parser


def _consoles() -> tuple[Console, Console]:
    return Console(), Console(stderr=True)


class EventStream:
    """Progress callback that prints one JSON object per line on stdout."""

    def __init__(self):
        self._lock = threading.Lock()

    def __call__(self, event) -> None:
        self._write(event.to_dict())

    def result(self, result: RunResult) -> None:
        self._write({"event": "result", **result.to_dict()})

    def _write(self, data: dict) -> None:
        with self._lock:
            print(json.dumps(data, ensure_ascii=False), flush=True)


@contextmanager
def _progress(args, label: str, err: Console):
    from batchvault import ui

    if args.json_events:
        yield EventStream()
    else:
        with ui.RunProgress(err, label, enabled=not args.quiet) as progress:
            yield progress


def _check_output_flags(args) -> None:
    if args.json and args.json_events:
        raise InputError("Use either --json or --json-events, not both.")


def _read_password(args) -> str | None:
    path = getattr(args, "password_file", None)
    if path:
        try:
            lines = Path(path).expanduser().read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise InputError(f"Cannot read password file {path}: {exc}") from exc
        if not lines or not lines[0]:
            raise InputError(f"Password file {path} is empty.")
        return lines[0]
    return os.environ.get(ENV_PASSWORD) or None


def _notify_url(args) -> str | None:
    url = args.notify or os.environ.get(ENV_NOTIFY_URL)
    if not url:
        return None

    notify.validate_url(url)
    if notify.is_insecure(url):
        _, err = _consoles()
        err.print(
            "[yellow]Warning: the notification URL uses http://, so the summary "
            "(file names and paths) is sent unencrypted. Prefer https://.[/yellow]"
        )
    return url


def _finish(
    args, command: str, result: RunResult, title: str | None, progress, url
) -> int:
    from batchvault import ui

    out, err = _consoles()
    if args.json_events:
        progress.result(result)
    elif args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    elif not args.quiet:
        ui.print_summary(out, result, title, command)

    if url and notify.should_notify(args.notify_on, result):
        problem = notify.send(url, notify.build_payload(command, result))
        if problem:
            log.warning("Notification failed: %s", redact(problem))
            err.print(
                f"[yellow]Warning: notification failed ({escape(problem)}).[/yellow]"
            )

    return 0 if result.status == Status.SUCCESS else 1


def _confirm_deletion(args, progress):
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
    return "auto (chosen for each file's type)" if level == AUTO else str(level)


def _describe_sources(args) -> str:
    parts = []
    if args.files:
        parts.append(f"{len(args.files)} given")
    if args.list:
        parts.append("list: " + ("standard input" if args.list == "-" else args.list))
    return ", ".join(parts) or "-"


def cmd_pack(args) -> int:
    from batchvault import ui

    _check_output_flags(args)
    if not args.files and not args.list:
        raise InputError("Give at least one FILE or use --list.")

    password = None
    if args.encrypt:
        password = _read_password(args)
        if not password:
            raise InputError(
                f"--encrypt needs a password: set {ENV_PASSWORD} "
                "or use --password-file."
            )
    url = _notify_url(args)

    out, err = _consoles()
    if not (args.quiet or args.json or args.json_events):
        ui.print_run_info(
            out,
            "BatchVault pack",
            [
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
            ],
        )

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
            confirm=_confirm_deletion(args, progress),
            progress=progress,
        )

    title = None  # the default title names the source files and archives
    if args.dry_run:
        title = "Pack plan (dry run): source files -> archives"
        if not (args.quiet or args.json or args.json_events):
            out.print("[bold]Dry run:[/bold] nothing was written.")
    return _finish(args, "pack", result, title, progress, url)


def cmd_unpack(args) -> int:
    from batchvault import ui

    _check_output_flags(args)
    password = _read_password(args)
    url = _notify_url(args)

    out, err = _consoles()
    if not (args.quiet or args.json or args.json_events):
        ui.print_run_info(
            out,
            "BatchVault unpack",
            [
                ("Archives", args.source),
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
            progress=progress,
        )
    return _finish(args, "unpack", result, None, progress, url)


def cmd_verify(args) -> int:
    from batchvault import ui

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


def cmd_inspect(args) -> int:
    from batchvault import ui

    entries = api.inspect(args.source)
    if args.json:
        print(json.dumps([e.to_dict() for e in entries], indent=2, ensure_ascii=False))
    elif not args.quiet:
        ui.print_inspect(Console(), entries, args.source)
    return 0


def cmd_keygen(args) -> int:
    private, public = Path(args.private).expanduser(), Path(args.public).expanduser()
    signing.generate_keypair(
        private,
        public,
        password=os.environ.get(ENV_KEY_PASSWORD) or None,
        overwrite=args.force,
    )
    if args.json:
        print(json.dumps({"private": str(private), "public": str(public)}))
    elif not args.quiet:
        out, _ = _consoles()
        out.print(f"Private key: {escape(str(private))} (keep it secret)")
        out.print(f"Public key:  {escape(str(public))} (share it)")
    return 0


def cmd_sign(args) -> int:
    manifest_path = Path(args.manifest).expanduser()
    Manifest.load(manifest_path)  # refuse to sign something that is not a manifest
    key = signing.load_private_key(
        Path(args.key).expanduser(), os.environ.get(ENV_KEY_PASSWORD) or None
    )
    signature = signing.sign_manifest(manifest_path, key)
    if args.json:
        print(json.dumps({"signature": str(signature)}))
    elif not args.quiet:
        _consoles()[0].print(f"Signature written to {escape(str(signature))}")
    return 0


def _write_listing(args, entries, noun: str) -> None:
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
    _, err = _consoles()
    entries = api.list_archives(args.folder)

    if not entries and not args.quiet:
        err.print(
            "[yellow]Warning: no archives found. "
            "Did you mean `batchvault list files`?[/yellow]"
        )
    _write_listing(args, entries, "archive")
    return 0


def cmd_doctor(args) -> int:
    from batchvault import ui

    checks = run_checks(args.seven_zip)
    if args.json:
        print(
            json.dumps(
                [vars(check) for check in checks], indent=2, ensure_ascii=False
            )
        )
    elif not args.quiet:
        ui.print_checks(Console(), checks)
    return 0 if passed(checks) else 1


def _use_utf8() -> None:
    # Keep file names readable when output is redirected on Windows.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _load_config(argv: list[str]) -> dict | None:
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args(argv)

    explicit = known.config or os.environ.get(ENV_CONFIG)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return load_config(path)  # named by the user, so trusted

    path = find_config()
    if path is None:
        return None
    config = load_config(path)
    reject_restricted(config, path)  # found by searching: not fully trusted
    return config


def main(argv: list[str] | None = None) -> int:
    _use_utf8()
    _, err = _consoles()
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        parser = build_parser(_load_config(argv))
    except BatchVaultError as exc:
        err.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
        return 2

    args = parser.parse_args(argv)

    log_path = None
    if not args.no_log:
        log_path = setup_logging(args.log_level, args.log_file)
    log.info("batchvault %s: %s", __version__, " ".join(redact_arguments(argv)))

    try:
        return args.handler(args)
    except BatchVaultError as exc:
        log.error("%s", exc)
        err.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
        return 2
    except KeyboardInterrupt:
        log.warning("Interrupted")
        err.print("[yellow]Interrupted.[/yellow]")
        return 130
    except Exception as exc:
        log.exception("Unexpected error")
        where = f" See the log: {log_path}" if log_path else ""
        err.print(f"[bold red]Unexpected error:[/bold red] {escape(str(exc))}.{where}")
        return 3
    finally:
        close_logging()
