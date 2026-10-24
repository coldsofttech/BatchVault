from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pathspec

from batchvault.errors import InputError


def load_patterns(values: Iterable[str]) -> list[str]:
    """Expand ``@file`` references into the patterns listed in that file."""
    patterns = []
    for value in values:
        if value.startswith("@"):
            path = Path(value[1:]).expanduser()
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except OSError as exc:
                raise InputError(f"Cannot read pattern file {path}: {exc}") from exc
            patterns.extend(
                line.strip()
                for line in lines
                if line.strip() and not line.lstrip().startswith("#")
            )
        elif value.strip():
            patterns.append(value.strip())
    return patterns


def _spec(patterns: list[str]):
    return pathspec.GitIgnoreSpec.from_lines(patterns) if patterns else None


class PathFilter:
    """Include/exclude filter using gitignore-style patterns (``*``, ``**``, ``/``).

    Exclude wins. If any include pattern is given, only matching paths are kept.
    """

    def __init__(self, include: Iterable[str] = (), exclude: Iterable[str] = ()):
        self._include = _spec(load_patterns(include))
        self._exclude = _spec(load_patterns(exclude))

    def matches(self, path: str) -> bool:
        path = path.replace("\\", "/").lstrip("/")
        if self._exclude and self._exclude.match_file(path):
            return False
        if self._include and not self._include.match_file(path):
            return False
        return True
