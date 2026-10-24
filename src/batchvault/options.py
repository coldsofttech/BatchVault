from __future__ import annotations

import re
from dataclasses import dataclass, field

from batchvault.errors import OptionsError

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB

_SIZE_UNITS = {"": 1, "k": KIB, "m": MIB, "g": GIB}
_SIZE_RE = re.compile(r"^\s*(\d+)\s*([kmg]?)b?\s*$", re.IGNORECASE)


# Compression formats of the native engine. The 7-Zip engine always writes 7z.
FORMATS = ("xz", "gzip", "bzip2", "store")


def parse_size(value: str | int) -> int:
    """Convert a size such as ``10m`` or ``1048576`` to bytes."""
    if isinstance(value, int):
        size = value
    else:
        match = _SIZE_RE.match(value)
        if not match:
            raise OptionsError(
                f"Invalid size {value!r}. Use a number with an optional "
                "k, m or g suffix, e.g. 500k, 10m, 2g."
            )
        size = int(match.group(1)) * _SIZE_UNITS[match.group(2).lower()]

    if size < 1:
        raise OptionsError("Size must be at least 1 byte.")
    return size


def format_size(size: int | None) -> str:
    """Human-readable size such as ``12.3 MiB``; ``-`` when unknown."""
    if size is None:
        return "-"
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


@dataclass(frozen=True)
class PackOptions:
    """Settings for creating an archive."""

    level: int = 5  # a fixed level; "auto" is resolved per file before this
    volume_size: int = 10 * MIB
    jobs: int = 0
    force: bool = False
    format: str = "xz"
    password: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.format not in FORMATS:
            raise OptionsError(
                f"Unknown format {self.format!r}. Choose from: {', '.join(FORMATS)}."
            )
        if not 0 <= self.level <= 9:
            raise OptionsError("Compression level must be between 0 and 9.")
        if self.volume_size < 1:
            raise OptionsError("Volume size must be at least 1 byte.")
        if self.jobs < 0:
            raise OptionsError("Jobs cannot be negative.")
