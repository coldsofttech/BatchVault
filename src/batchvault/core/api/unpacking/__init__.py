"""The ``unpack`` operation of the public API, loaded on first use."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from batchvault.core.api.unpacking.run import unpack

__all__ = ["unpack"]


def __getattr__(name: str) -> object:
    if name != "unpack":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from batchvault.core.api.unpacking.run import unpack

    globals()[name] = unpack
    return unpack


def __dir__() -> list[str]:
    return sorted(__all__)
