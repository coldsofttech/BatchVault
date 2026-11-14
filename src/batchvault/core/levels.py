"""Pick a compression level for each file from its type (``--level auto``).

The choice uses only the file's signature (its first bytes) and extension, so it
is instant and predictable. Level 0 means "do not try": the file is already
compressed, so a stronger level would only cost time and memory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from batchvault.core.errors import OptionsError

AUTO = "auto"


@dataclass(frozen=True)
class LevelChoice:
    level: int
    kind: str  # a short reason, shown next to the result


def parse_level(value: int | str) -> int | str:
    """Validate a level: 0 to 9, or ``"auto"``."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text == AUTO:
            return AUTO
        try:
            value = int(text)
        except ValueError:
            raise OptionsError(
                f"Invalid compression level {value!r}. Use 0-9 or auto."
            ) from None

    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 9:
        raise OptionsError("Compression level must be between 0 and 9, or auto.")
    return value


def level_argument(text: str) -> int | str:
    """argparse type for ``--level``; the range is checked by ``choices``."""
    value = text.strip().lower()
    if value == AUTO:
        return AUTO
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid level {text!r}: use 0-9 or auto"
        ) from None


# (offset, bytes) at the start of a file that mark an already compressed format.
_SIGNATURES = (
    (0, b"PK\x03\x04"),  # zip, docx, jar, apk, epub, ...
    (0, b"PK\x05\x06"),
    (0, b"\x1f\x8b"),  # gzip
    (0, b"\xfd7zXZ\x00"),  # xz
    (0, b"7z\xbc\xaf\x27\x1c"),
    (0, b"Rar!\x1a\x07"),
    (0, b"\x28\xb5\x2f\xfd"),  # zstd
    (0, b"\x04\x22\x4d\x18"),  # lz4
    (0, b"MSCF"),  # cab
    (0, b"\xff\xd8\xff"),  # jpeg
    (0, b"\x89PNG\r\n\x1a\n"),
    (0, b"GIF87a"),
    (0, b"GIF89a"),
    (4, b"ftyp"),  # mp4, mov, heic, m4a
    (0, b"\x1a\x45\xdf\xa3"),  # matroska, webm
    (0, b"OggS"),
    (0, b"fLaC"),
    (0, b"\x00\x00\x01\xba"),  # mpeg program stream
)


@dataclass(frozen=True)
class _Group:
    kind: str
    level: int
    extensions: frozenset[str]


@dataclass(frozen=True)
class _Rules:
    default_level: int
    default_kind: str
    groups: dict[str, _Group]  # in file order; the first group is "incompressible"


@cache
def _rules() -> _Rules:
    """Read ``data/levels.toml``, which says which level suits which file type."""
    import tomllib
    from importlib.resources import files

    resource = files("batchvault.core").joinpath("data", "levels.toml")
    data = tomllib.loads(resource.read_text(encoding="utf-8"))
    default_level = data.pop("default_level")
    default_kind = data.pop("default_kind")
    groups = {
        name: _Group(
            kind=group["kind"],
            level=group["level"],
            extensions=frozenset(group["extensions"]),
        )
        for name, group in data.items()
    }
    for level in (default_level, *[group.level for group in groups.values()]):
        if isinstance(level, bool) or not isinstance(level, int) or not 0 <= level <= 9:
            raise ValueError(f"levels.toml has a level outside 0-9: {level!r}")
    return _Rules(default_level, default_kind, groups)


def __getattr__(name: str) -> int:
    """``DEFAULT_LEVEL`` and ``<GROUP>_LEVEL`` (``TEXT_LEVEL``), read on first use."""
    rules = _rules()
    if name == "DEFAULT_LEVEL":
        return rules.default_level
    for group_name, group in rules.groups.items():
        if name == f"{group_name.upper()}_LEVEL":
            return group.level
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _has_compressed_signature(head: bytes) -> bool:
    if any(head[at : at + len(mark)] == mark for at, mark in _SIGNATURES):
        return True
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return True
    # bzip2: "BZh", a block size digit and the block magic. The longer check keeps
    # a text file that happens to start with "BZh" from being misjudged.
    if head[:3] == b"BZh" and head[3:4] in b"123456789" and head[4:10] == b"1AY&SY":
        return True
    # ID3v2 tag (mp3): version, flags, then a size whose bytes are below 0x80.
    return (
        head[:3] == b"ID3"
        and head[3:4] in (b"\x02", b"\x03", b"\x04")
        and head[4:5] == b"\x00"
        and len(head) >= 10
        and all(byte < 0x80 for byte in head[6:10])
    )


def choose_level(path: Path) -> LevelChoice:
    """The best compression level for this file, judged by its type."""
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        head = b""

    rules = _rules()
    incompressible = rules.groups["incompressible"]
    if _has_compressed_signature(head):
        return LevelChoice(incompressible.level, incompressible.kind)

    extension = path.suffix.lower().lstrip(".")
    for group in rules.groups.values():
        if extension in group.extensions:
            return LevelChoice(group.level, group.kind)
    return LevelChoice(rules.default_level, rules.default_kind)
