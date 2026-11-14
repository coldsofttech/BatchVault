"""The ``pack`` operation of the public API, loaded on first use."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from batchvault.core.api.packing.run import pack

__all__ = ["pack"]


def __getattr__(name: str) -> object:
    if name != "pack":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from batchvault.core.api.packing.run import pack

    globals()[name] = pack
    return pack


def __dir__() -> list[str]:
    return sorted(__all__)
