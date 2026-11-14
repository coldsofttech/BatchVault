"""Public Python API. The command-line interface is a thin wrapper over this.

Each operation lives in its own module and is loaded on first use, so importing
one of them does not pull in the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from batchvault.core.api.listing import inspect, list_archives, list_files
    from batchvault.core.api.packing.run import pack
    from batchvault.core.api.unpacking.run import unpack
    from batchvault.core.api.verifying import verify

__all__ = [
    "inspect",
    "list_archives",
    "list_files",
    "pack",
    "unpack",
    "verify",
]

# Public name to the module that defines it.
_LAZY = {
    "inspect": "batchvault.core.api.listing",
    "list_archives": "batchvault.core.api.listing",
    "list_files": "batchvault.core.api.listing",
    "pack": "batchvault.core.api.packing.run",
    "unpack": "batchvault.core.api.unpacking.run",
    "verify": "batchvault.core.api.verifying",
}


def __getattr__(name: str) -> object:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    value = getattr(import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(__all__)
