from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable
from pathlib import Path

from batchvault.errors import InputError
from batchvault.filters import PathFilter
from batchvault.manifest import MANIFEST_NAME
from batchvault.volumes import split_volume_name

# A text list written by ``list files`` starts with this comment. It records the
# folder the files were listed from, so ``pack`` keeps that folder's name.
ROOT_HEADER = "# batchvault-root:"

ListItem = tuple[str, str, str | None]  # (label, path, root the list recorded)


def read_list_items(list_path: Path) -> list[ListItem]:
    """Read a list file as ``(label, path, root)`` items.

    Text lists have one path per line; blank lines and ``#`` comments are
    ignored, except the ``# batchvault-root:`` header written by ``list files``.
    JSON lists are an array of paths or ``{"path": ..., "root": ...}`` objects (as
    printed by ``batchvault list files``), or an object with such an array under
    ``"files"``. ``root`` is the folder the file was listed from, if recorded.
    """
    if str(list_path) == "-":
        list_path = Path("<stdin>")
        text = sys.stdin.read().lstrip("﻿")
    else:
        try:
            text = list_path.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise InputError(f"Cannot read list file {list_path}: {exc}") from exc

    if list_path.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
        return _json_items(list_path, text)

    items = []
    root = None
    for number, line in enumerate(text.splitlines(), start=1):
        entry = line.strip()
        if entry.startswith(ROOT_HEADER):
            root = entry[len(ROOT_HEADER) :].strip() or None
        elif entry and not entry.startswith("#"):
            items.append((f"{list_path}:{number}", entry, root))
    return items


def read_list_entries(list_path: Path) -> list[tuple[str, str]]:
    """Read a list file as ``(label, path)`` pairs (see ``read_list_items``)."""
    return [(label, entry) for label, entry, _ in read_list_items(list_path)]


def _json_items(list_path: Path, text: str) -> list[ListItem]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"{list_path} is not valid JSON: {exc}") from exc

    default_root = None
    if isinstance(data, dict):
        default_root = data.get("root") if isinstance(data.get("root"), str) else None
        data = data.get("files")
    if not isinstance(data, list):
        raise InputError(f"{list_path}: expected a JSON array of paths")

    items = []
    for index, item in enumerate(data, start=1):
        value = item.get("path") if isinstance(item, dict) else item
        if not isinstance(value, str) or not value.strip():
            raise InputError(f"{list_path}: item {index} has no path")
        root = item.get("root") if isinstance(item, dict) else None
        if not isinstance(root, str) or not root.strip():
            root = default_root
        items.append((f"{list_path}[{index}]", value.strip(), root))
    return items


def relative_folder(path: Path, root: Path) -> str:
    """The folder of ``path`` relative to ``root``, with forward slashes.

    Returns ``""`` when the file sits directly in ``root``.
    """
    try:
        relative = path.parent.relative_to(root)
    except ValueError:
        raise InputError(
            f"{path} is not inside the root folder {root}. Choose a root that "
            "contains all the files, or leave --root out."
        ) from None
    return relative.as_posix() if relative.parts else ""


def assign_folders(
    items: list[tuple[Path, Path | None]], root: Path | None = None
) -> dict[Path, str]:
    """Decide the folder each file is stored under (``""`` = not in a subfolder).

    - With an explicit ``root``, every file is stored relative to it.
    - A file from a ``list files`` list is stored relative to the folder that
      listing recorded, so the listed folder's own name is kept.
    - Other files are stored relative to the deepest folder they all share, so the
      folders below it are kept and files directly in it are not nested.
    """
    if root is not None:
        return {path: relative_folder(path, root) for path, _ in items}

    folders: dict[Path, str] = {}
    unlisted = []
    for path, recorded in items:
        if recorded is not None:
            try:
                folders[path] = relative_folder(path, recorded)
                continue
            except InputError:
                pass  # the list was edited since it was made; use the shared folder
        unlisted.append(path)

    if unlisted:
        try:
            shared = Path(os.path.commonpath([str(p.parent) for p in unlisted]))
        except ValueError:
            raise InputError(
                "These files are on different drives, so there is no folder they "
                "share. Archive each drive separately, or pass --root."
            ) from None
        for path in unlisted:
            folders[path] = relative_folder(path, shared)
    return folders


def looks_like_archive(path: Path) -> bool:
    return split_volume_name(path.name) is not None or path.name == MANIFEST_NAME


def gather_sources(
    files: Iterable[str | Path] = (),
    list_file: str | Path | None = None,
    *,
    path_filter: PathFilter | None = None,
    allow_archives: bool = False,
    root: Path | None = None,
) -> list[tuple[Path, str]]:
    """Turn arguments and/or a list file into unique, existing source files.

    Returns ``(file, folder)`` pairs, where ``folder`` is the folder the file is
    stored under (see ``assign_folders``). Relative paths are resolved from the
    current directory. Files with the same name are fine when their folders
    differ.
    """
    if root is not None:
        root = Path(root).expanduser().resolve()

    items: list[ListItem] = [("argument", str(file), None) for file in files]
    if list_file is not None:
        items += read_list_items(
            Path(list_file) if str(list_file) == "-" else Path(list_file).expanduser()
        )

    sources: dict[str, tuple[Path, Path | None]] = {}

    for label, entry, recorded in items:
        path = Path(entry).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if not path.is_file():
            raise InputError(f"{label}: source file not found: {entry}")

        path = path.resolve()
        if looks_like_archive(path) and not allow_archives:
            raise InputError(
                f"{label}: {path.name} looks like an archive volume or manifest. "
                "Use allow_archives / --allow-archives to archive it anyway."
            )
        if path_filter and not path_filter.matches(path.as_posix()):
            continue

        key = os.path.normcase(str(path))
        if key not in sources:
            hint = Path(recorded).expanduser().resolve() if recorded else None
            sources[key] = (path, hint)

    if not sources:
        raise InputError("No input files to archive.")

    folders = assign_folders(list(sources.values()), root)

    # Each file gets its own archive folder named after it, inside its stored
    # folder; two files may not end up in the same place.
    taken: dict[tuple[str, str], Path] = {}
    for path, _ in sources.values():
        place = (folders[path].casefold(), path.name.casefold())
        if place in taken:
            raise InputError(
                "Two source files would use the same archive folder: "
                f"{taken[place]} and {path}. Archive them separately, or keep "
                "their folders apart with --root."
            )
        taken[place] = path

    return [(path, folders[path]) for path, _ in sources.values()]


def collect_sources(
    files: Iterable[str | Path] = (),
    list_file: str | Path | None = None,
    *,
    path_filter: PathFilter | None = None,
    allow_archives: bool = False,
    root: Path | None = None,
) -> list[Path]:
    """Like ``gather_sources``, returning only the files."""
    return [
        path
        for path, _ in gather_sources(
            files,
            list_file,
            path_filter=path_filter,
            allow_archives=allow_archives,
            root=root,
        )
    ]
