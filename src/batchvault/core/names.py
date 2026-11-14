"""Checks on file and folder names before they are written to disk."""

from __future__ import annotations

_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}  # fmt: skip
_WINDOWS_BAD_CHARACTERS = '<>:"|?*'


def unsafe_on_windows(name: str) -> bool:
    """Names Windows treats specially: drives (``C:x``), streams (``a:b``),
    devices (``NUL``, ``con.txt``), and names ending in a dot or space."""
    stem = name.split(".")[0].rstrip(" ").lower()
    return (
        any(char in name for char in _WINDOWS_BAD_CHARACTERS)
        or name != name.rstrip(" .")
        or stem in _WINDOWS_RESERVED
    )


def validate_folder(folder: str) -> str:
    """Check a stored folder path (forward slashes, relative, no ``..``).

    Returns it unchanged; raises ValueError if it could point outside the folder
    it is restored into.
    """
    if folder == "":
        return folder
    if "\\" in folder or "\x00" in folder or folder.startswith("/"):
        raise ValueError(f"unsafe folder {folder!r}")
    if any(part in ("", ".", "..") for part in folder.split("/")):
        raise ValueError(f"unsafe folder {folder!r}")
    return folder
