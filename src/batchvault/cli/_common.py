"""Helpers shared by the command-line commands."""

from __future__ import annotations

import json
import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from batchvault.cli import notify
from batchvault.core.errors import InputError
from batchvault.core.models import RunResult, Status
from batchvault.core.security import redact

log = logging.getLogger(__name__)


ENV_PASSWORD = "BATCHVAULT_PASSWORD"


ENV_KEY_PASSWORD = "BATCHVAULT_KEY_PASSWORD"


ENV_NOTIFY_URL = "BATCHVAULT_NOTIFY_URL"


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
    from batchvault.cli import ui

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
    from batchvault.cli import ui

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


def _given(args, dest: str, names: tuple[str, ...]) -> bool:
    """True if the user passed an option: on the command line or with a new value.

    A value from the settings file is the parser's default, so it never counts.
    """
    if getattr(args, dest, None) != args.command_parser.get_default(dest):
        return True
    for token in args.argv:
        for name in names:
            if token == name or token.startswith(name + "="):
                return True
            if not name.startswith("--") and token.startswith(name):
                return True
    return False


def _reject_given(args, fixed: dict[str, tuple[str, ...]], extra: list[str]) -> None:
    clashing = extra + [
        names[0] for dest, names in fixed.items() if _given(args, dest, names)
    ]
    if clashing:
        raise InputError(
            "--resume continues the earlier run with its own settings, so do not "
            f"also pass: {', '.join(clashing)}. Remove them, or run without "
            "--resume to start again."
        )
