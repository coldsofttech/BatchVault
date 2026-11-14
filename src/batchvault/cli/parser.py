"""The argparse definition of every command and option."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def _parents() -> dict[str, argparse.ArgumentParser]:
    import argparse

    from batchvault.cli import notify
    from batchvault.cli._common import ENV_NOTIFY_URL, ENV_PASSWORD
    from batchvault.cli.logs import LOG_LEVELS

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
    from batchvault.core.config import defaults_for

    if not config:
        return
    actions = {action.dest: action for action in subparser._actions}
    defaults = defaults_for(config, name, actions)
    if defaults:
        subparser.set_defaults(**defaults)


def build_parser(config: dict | None = None) -> argparse.ArgumentParser:
    import argparse

    from batchvault import __version__
    from batchvault.cli._common import ENV_KEY_PASSWORD, ENV_PASSWORD
    from batchvault.cli.commands.doctor import cmd_doctor
    from batchvault.cli.commands.inspect import cmd_inspect
    from batchvault.cli.commands.keys import cmd_keygen, cmd_sign
    from batchvault.cli.commands.listing import cmd_list_archives, cmd_list_files
    from batchvault.cli.commands.pack import cmd_pack
    from batchvault.cli.commands.unpack import cmd_unpack
    from batchvault.cli.commands.verify import cmd_verify
    from batchvault.core.levels import AUTO, level_argument
    from batchvault.core.options import FORMATS
    from batchvault.engines.registry import ENGINE_CHOICES

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
        "--no-readme",
        dest="readme",
        action="store_false",
        help="Do not write BATCHVAULT-README.md next to the manifest.",
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
    pack.add_argument(
        "--resume",
        action="store_true",
        help="Continue an interrupted run in the output folder. Every setting "
        "comes from the earlier run, so give only -o (and a password or key).",
    )
    pack.set_defaults(handler=cmd_pack, command_parser=pack)
    _configure(pack, "pack", config)

    unpack = commands.add_parser(
        "unpack",
        parents=[common, jobs, seven, run, retry, public],
        help="Restore files from archives.",
        description="Restore files from a manifest, folder, volume or list file.",
        epilog="Example: batchvault unpack archives -o restored",
    )
    unpack.add_argument(
        "source",
        nargs="?",
        metavar="SOURCE",
        help="Manifest, folder, volume or list file (not needed with --resume).",
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
    unpack.add_argument(
        "--resume",
        action="store_true",
        help="Continue an interrupted run into the output folder, using the "
        "source of the earlier run (give only -o).",
    )
    unpack.set_defaults(handler=cmd_unpack, command_parser=unpack)
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
