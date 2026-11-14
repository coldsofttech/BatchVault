from __future__ import annotations

import logging
from typing import TYPE_CHECKING

__version__ = "0.1.1"

# Library code only logs; the command-line interface decides where logs go.
logging.getLogger("batchvault").addHandler(logging.NullHandler())

if TYPE_CHECKING:
    from batchvault.core.api import (
        inspect,
        list_archives,
        list_files,
        pack,
        unpack,
        verify,
    )
    from batchvault.core.errors import (
        BatchVaultError,
        ConfigError,
        EngineError,
        EngineNotFoundError,
        InputError,
        ManifestError,
        OptionsError,
        OutputExistsError,
        ResumeError,
        SignatureError,
        VolumeError,
    )
    from batchvault.core.models import (
        FileEntry,
        FileResult,
        InspectEntry,
        ProgressEvent,
        RunResult,
        Status,
    )

__all__ = [
    "BatchVaultError",
    "ConfigError",
    "EngineError",
    "EngineNotFoundError",
    "FileEntry",
    "FileResult",
    "InputError",
    "InspectEntry",
    "ManifestError",
    "OptionsError",
    "OutputExistsError",
    "ProgressEvent",
    "ResumeError",
    "RunResult",
    "SignatureError",
    "Status",
    "VolumeError",
    "__version__",
    "inspect",
    "list_archives",
    "list_files",
    "pack",
    "unpack",
    "verify",
]

# Public name to the module that defines it. Loaded on first use, so importing a
# submodule such as `batchvault.core.options` does not pull in the whole API.
_LAZY = {
    "inspect": "batchvault.core.api",
    "list_archives": "batchvault.core.api",
    "list_files": "batchvault.core.api",
    "pack": "batchvault.core.api",
    "unpack": "batchvault.core.api",
    "verify": "batchvault.core.api",
    "BatchVaultError": "batchvault.core.errors",
    "ConfigError": "batchvault.core.errors",
    "EngineError": "batchvault.core.errors",
    "EngineNotFoundError": "batchvault.core.errors",
    "InputError": "batchvault.core.errors",
    "ManifestError": "batchvault.core.errors",
    "OptionsError": "batchvault.core.errors",
    "OutputExistsError": "batchvault.core.errors",
    "ResumeError": "batchvault.core.errors",
    "SignatureError": "batchvault.core.errors",
    "VolumeError": "batchvault.core.errors",
    "FileEntry": "batchvault.core.models",
    "FileResult": "batchvault.core.models",
    "InspectEntry": "batchvault.core.models",
    "ProgressEvent": "batchvault.core.models",
    "RunResult": "batchvault.core.models",
    "Status": "batchvault.core.models",
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
