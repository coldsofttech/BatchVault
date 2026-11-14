"""Command-line interface: a thin wrapper over :mod:`batchvault.core.api`."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


ENV_CONFIG = "BATCHVAULT_CONFIG"


def _use_utf8() -> None:
    # Keep file names readable when output is redirected on Windows.
    import io
    import sys

    for stream in (sys.stdout, sys.stderr):
        if not isinstance(stream, io.TextIOWrapper):
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def _load_config(argv: list[str]) -> dict | None:
    import argparse
    import os
    from pathlib import Path

    from batchvault.core.config import find_config, load_config, reject_restricted
    from batchvault.core.errors import ConfigError

    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--config")
    known, _ = pre.parse_known_args(argv)

    explicit = known.config or os.environ.get(ENV_CONFIG)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"Config file not found: {path}")
        return load_config(path)  # named by the user, so trusted

    found = find_config()
    if found is None:
        return None
    config = load_config(found)
    reject_restricted(config, found)  # found by searching: not fully trusted
    return config


def main(argv: list[str] | None = None) -> int:
    import sys

    from rich.markup import escape

    from batchvault import __version__
    from batchvault.cli._common import _consoles
    from batchvault.cli.logs import close_logging, setup_logging
    from batchvault.cli.parser import build_parser
    from batchvault.core.errors import BatchVaultError
    from batchvault.core.security import redact_arguments

    _use_utf8()
    _, err = _consoles()
    argv = list(sys.argv[1:] if argv is None else argv)

    try:
        parser = build_parser(_load_config(argv))
    except BatchVaultError as exc:
        err.print(f"[bold red]Error:[/bold red] {escape(str(exc))}")
        return 2

    args = parser.parse_args(argv)
    args.argv = argv

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
