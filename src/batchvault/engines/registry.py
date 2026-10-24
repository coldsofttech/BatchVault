from __future__ import annotations

from pathlib import Path

from batchvault.engines.base import Engine
from batchvault.engines.native import NativeEngine
from batchvault.engines.sevenzip import SevenZipEngine, find_7zip
from batchvault.errors import InputError
from batchvault.volumes import split_volume_name

ENGINE_CHOICES = ("auto", "native", "7zip")


def get_engine(name: str = "auto", seven_zip: str | Path | None = None) -> Engine:
    """Return the engine to create archives with.

    ``auto`` uses 7-Zip when it is found (or when a path to it was given) and
    falls back to the native engine.
    """
    if name == "native":
        return NativeEngine()

    if name in ("7zip", "7z"):
        find_7zip(seven_zip)  # raises EngineNotFoundError with install help
        return SevenZipEngine(seven_zip)

    if name == "auto":
        if seven_zip:
            find_7zip(seven_zip)
            return SevenZipEngine(seven_zip)
        sevenzip = SevenZipEngine()
        return sevenzip if sevenzip.is_available() else NativeEngine()

    choices = ", ".join(ENGINE_CHOICES)
    raise InputError(f"Unknown engine {name!r}. Choose from: {choices}.")


def engine_for_volume(volume: Path, seven_zip: str | Path | None = None) -> Engine:
    """Pick the engine that can read a volume, from its file name."""
    parsed = split_volume_name(volume.name)
    if parsed is None:
        raise InputError(f"Not an archive volume: {volume}")
    if parsed[0].lower().endswith(".7z"):
        find_7zip(seven_zip)
        return SevenZipEngine(seven_zip)
    return NativeEngine()


def option_warnings(
    engine: Engine, format_given: str | None, level: int
) -> list[str]:
    """Settings the chosen engine cannot honour exactly."""
    warnings = []
    if engine.name == "7zip" and format_given not in (None, "xz"):
        warnings.append(
            f"The 7-Zip engine always writes 7z (LZMA2); --format {format_given} "
            "is ignored."
        )
    if engine.name == "native" and format_given == "bzip2" and level == 0:
        warnings.append("bzip2 has no level 0; level 1 was used.")
    return warnings
