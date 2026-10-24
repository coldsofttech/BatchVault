"""Rich terminal output. Only the command-line interface imports this module."""

from __future__ import annotations

import threading
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from batchvault.doctor import Check
from batchvault.eta import EtaEstimator, format_duration
from batchvault.models import InspectEntry, ProgressEvent, RunResult, Status
from batchvault.options import format_size

STATUS_STYLE = {
    Status.SUCCESS: ("OK", "green"),
    Status.SKIPPED: ("SKIPPED", "yellow"),
    Status.FAILED: ("FAILED", "bold red"),
    Status.PARTIAL: ("PARTIAL", "yellow"),
}


class EtaColumn(ProgressColumn):
    """Shows the run's own ETA for tasks created with an ``estimator`` field."""

    def render(self, task):
        estimator = task.fields.get("estimator")
        if estimator is None:
            return Text("")

        remaining = estimator.estimate(task.completed, task.total or 0)
        if remaining is None:
            return Text("ETA estimating…", style="dim")
        return Text(f"ETA {format_duration(remaining)}", style="progress.remaining")


def status_text(status: Status) -> Text:
    label, style = STATUS_STYLE[status]
    return Text(label, style=style)


class RunProgress:
    """Progress callback that draws an overall bar and one line per active file.

    Use as a context manager and pass the instance as ``progress=``. With
    ``enabled=False`` it does nothing.
    """

    def __init__(self, console: Console, label: str, enabled: bool = True):
        self._enabled = enabled
        self._label = label
        self._lock = threading.Lock()
        self._eta = EtaEstimator()
        self._by_bytes = False
        self._overall = None
        self._tasks: dict[Path, int] = {}
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            EtaColumn(),
            console=console,
            transient=True,
            disable=not enabled,
        )

    def __enter__(self):
        if self._enabled:
            self._progress.start()
        return self

    def __exit__(self, *exc_info):
        self._progress.stop()

    def pause(self) -> None:
        """Stop drawing, e.g. before asking the user a question."""
        self._progress.stop()

    def __call__(self, event: ProgressEvent) -> None:
        if not self._enabled:
            return

        with self._lock:
            if event.kind == "plan":
                self._by_bytes = event.size > 0
                total = event.size if self._by_bytes else event.count
                self._overall = self._progress.add_task(
                    self._label, total=total, estimator=self._eta
                )
                self._eta.start()
            elif event.kind == "start":
                task = self._progress.add_task(escape(event.source.name), total=None)
                self._tasks[event.source] = task
            elif event.kind == "finish":
                self._finish(event)

    def _finish(self, event: ProgressEvent) -> None:
        task = self._tasks.pop(event.source, None)
        if task is not None:
            self._progress.remove_task(task)
        if self._overall is not None:
            self._progress.advance(
                self._overall, event.size if self._by_bytes else 1
            )

        label, style = STATUS_STYLE.get(event.status, ("", ""))
        self._progress.console.print(
            Text.assemble((f"{label:<8}", style), event.source.name)
        )


def print_run_info(console: Console, title: str, rows: list[tuple[str, str]]) -> None:
    info = Table.grid(padding=(0, 2))
    info.add_column(style="bold cyan")
    info.add_column()
    for key, value in rows:
        info.add_row(key, Text(value))
    console.print(Panel(info, title=title, border_style="cyan"))


# Terms used on screen: a SOURCE is an original file; an ARCHIVE is the set of
# volumes made from it. Each table names its columns accordingly.
SOURCE_STYLE = "cyan"
ARCHIVE_STYLE = "magenta"

SUMMARY_TITLES = {
    "pack": "Pack: source files -> archives",
    "unpack": "Unpack: archives -> restored files",
    "verify": "Verify: archives",
}


def _shown(folder: str, name: str) -> str:
    return f"{folder}/{name}" if folder else name


def _detail_text(entry) -> Text:
    style = "dim" if entry.status == Status.SUCCESS else ""
    text = Text(entry.message, style=style)
    for warning in entry.warnings:
        text.append(f"\n{warning}", style="yellow")
    return text


def print_summary(
    console: Console, result: RunResult, title: str | None = None, kind: str = "pack"
) -> None:
    """Print one row per file. ``kind`` is pack, unpack or verify."""
    table = Table(title=title or SUMMARY_TITLES.get(kind, ""), show_lines=False)

    # The same columns as `inspect`: the original file, its archive, the outcome
    # and an explanation.
    table.add_column("Source", style=SOURCE_STYLE, overflow="fold")
    table.add_column("Archive", style=ARCHIVE_STYLE, overflow="fold")
    table.add_column("Status")
    table.add_column("Details", overflow="fold")

    for entry in result.entries:
        # pack: the source is the original file and the archive its first volume.
        # unpack and verify: the "source" is the archive's first volume.
        if kind == "pack":
            archive = entry.archive.name if entry.archive else "-"
            original = entry.name or entry.source.name
        else:
            archive = entry.source.name
            original = entry.name or "-"
        table.add_row(
            Text(_shown(entry.folder, original)),
            Text(archive),
            status_text(entry.status),
            _detail_text(entry),
        )
    console.print(table)

    for warning in result.warnings:
        console.print(Text(f"Warning: {warning}", style="yellow"))

    counts = ", ".join(
        f"{result.count(status)} {label.lower()}"
        for status, (label, _) in STATUS_STYLE.items()
        if status != Status.PARTIAL and result.count(status)
    )
    console.print(f"[bold]Summary:[/bold] {escape(counts or 'nothing to do')}")
    if result.manifest and any(e.status == Status.SUCCESS for e in result.entries):
        console.print(f"Manifest: {escape(str(result.manifest))}")


def _archive_status(entry: InspectEntry) -> Text:
    if entry.complete is True:
        return Text("OK", style="green")
    if entry.complete is False:
        return Text("MISSING", style="bold red")
    return Text("-", style="dim")  # no manifest, so completeness is unknown


def _inspect_details(entry: InspectEntry) -> Text:
    notes = []
    if entry.encrypted:
        notes.append(("encrypted", "yellow"))
    if entry.complete is False:
        notes.append(("some volumes are missing", "bold red"))
    if entry.complete is None:
        notes.append(("no manifest: sizes and checks unknown", "dim"))

    text = Text()
    for index, (note, style) in enumerate(notes):
        if index:
            text.append(", ")
        text.append(note, style=style)
    return text


def print_inspect(
    console: Console, entries: list[InspectEntry], source: str | None = None
) -> None:
    """Describe archives with the same layout and colours as the result tables."""
    original = sum(e.size or 0 for e in entries)
    stored = sum(e.stored_size or 0 for e in entries)
    missing = sum(1 for e in entries if e.complete is False)
    encrypted = sum(1 for e in entries if e.encrypted)

    rows = []
    if source:
        rows.append(("Archives", source))
    rows.append(("Files", str(len(entries))))
    if original:
        sizes = f"{format_size(original)} -> {format_size(stored)}"
        rows.append(("Size", f"{sizes} ({stored / original:.0%})"))
    else:
        rows.append(("Stored size", format_size(stored)))
    if encrypted:
        rows.append(("Encrypted", f"{encrypted} of {len(entries)}"))
    print_run_info(console, "BatchVault inspect", rows)

    table = Table(title="Inspect: archives", show_lines=False)
    table.add_column("Source", style=SOURCE_STYLE, overflow="fold")
    table.add_column("Archive", style=ARCHIVE_STYLE, overflow="fold")
    table.add_column("Status")
    table.add_column("Engine")
    table.add_column("Format")
    table.add_column("Level", justify="right")
    table.add_column("Original", justify="right")
    table.add_column("Stored", justify="right")
    table.add_column("Ratio", justify="right")
    table.add_column("Volumes", justify="right")
    table.add_column("Details", overflow="fold")

    for entry in entries:
        ratio = f"{entry.ratio:.0%}" if entry.ratio is not None else "-"
        table.add_row(
            Text(_shown(entry.folder, entry.name)),
            Text(entry.path.name),
            _archive_status(entry),
            entry.engine,
            entry.format or "-",
            "-" if entry.level is None else str(entry.level),
            format_size(entry.size),
            format_size(entry.stored_size),
            ratio,
            str(entry.volumes),
            _inspect_details(entry),
        )
    console.print(table)

    summary = f"{len(entries)} archive{'' if len(entries) == 1 else 's'}"
    if missing:
        summary += f", {missing} with missing volumes"
    console.print(f"[bold]Summary:[/bold] {escape(summary)}")


def print_checks(console: Console, checks: list[Check]) -> None:
    table = Table(title="BatchVault doctor")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Details", overflow="fold")

    for check in checks:
        if check.ok:
            result = Text("OK", style="green")
        else:
            result = Text("FAILED" if check.required else "MISSING", style="red")
        table.add_row(check.name, result, Text(check.detail))
    console.print(table)
