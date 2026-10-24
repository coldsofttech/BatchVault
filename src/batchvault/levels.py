"""Pick a compression level for each file from its type (``--level auto``).

The choice uses only the file's signature (its first bytes) and extension, so it
is instant and predictable. Level 0 means "do not try": the file is already
compressed, so a stronger level would only cost time and memory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from batchvault.errors import OptionsError

AUTO = "auto"
DEFAULT_LEVEL = 5

TEXT_LEVEL = 6
MEDIA_LEVEL = 6
BINARY_LEVEL = 5
PDF_LEVEL = 3
INCOMPRESSIBLE_LEVEL = 0


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


def _extensions(text: str) -> frozenset[str]:
    return frozenset(text.split())


# Formats that are already compressed (or encrypted): nothing left to squeeze.
INCOMPRESSIBLE = _extensions(
    """
    zip 7z rar gz tgz xz txz bz2 tbz2 zst lz4 lzma z cab jar war ear apk ipa
    docx xlsx pptx odt ods odp epub
    jpg jpeg png gif webp heic heif avif cr2 nef arw dng orf rw2
    mp4 m4v mov mkv webm avi wmv flv mpg mpeg m2ts 3gp
    mp3 aac m4a ogg oga opus flac wma wv
    """
)

# Plain text and similar: very repetitive, so a strong level pays off.
TEXT = _extensions(
    """
    txt log csv tsv json jsonl ndjson xml html htm xhtml css scss sass less
    js mjs jsx tsx md markdown rst yaml yml toml ini cfg conf properties sql
    py pyi java kt c h cc cpp hpp cs go rs rb php swift sh bash zsh bat cmd ps1
    tex bib srt vtt ass svg rtf eml ics vcf gpx kml diff patch gradle tf
    ipynb r pl lua dart scala vue
    """
)

# Uncompressed pictures and sound.
UNCOMPRESSED_MEDIA = _extensions(
    "wav aiff aif bmp tif tiff ppm pgm pbm tga dds pcm psd"
)

# Programs, disk images, databases and containers: compressible, but the
# strongest levels gain little for the memory they need.
BINARY = _extensions(
    """
    exe dll so dylib o a lib class pyc wasm iso img vhd vhdx vmdk qcow2 vdi
    db sqlite sqlite3 mdb bak dump pak tar
    """
)

PDF = _extensions("pdf")

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

    if _has_compressed_signature(head):
        return LevelChoice(INCOMPRESSIBLE_LEVEL, "already compressed")

    extension = path.suffix.lower().lstrip(".")
    if extension in INCOMPRESSIBLE:
        return LevelChoice(INCOMPRESSIBLE_LEVEL, "already compressed")
    if extension in TEXT:
        return LevelChoice(TEXT_LEVEL, "text")
    if extension in UNCOMPRESSED_MEDIA:
        return LevelChoice(MEDIA_LEVEL, "uncompressed media")
    if extension in BINARY:
        return LevelChoice(BINARY_LEVEL, "binary data")
    if extension in PDF:
        return LevelChoice(PDF_LEVEL, "pdf")
    return LevelChoice(DEFAULT_LEVEL, "unknown type")
