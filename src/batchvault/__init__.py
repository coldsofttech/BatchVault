import logging

__version__ = "0.1.0"

# Library code only logs; the command-line interface decides where logs go.
logging.getLogger("batchvault").addHandler(logging.NullHandler())

from batchvault.api import (  # noqa: E402
    inspect,
    list_archives,
    list_files,
    pack,
    unpack,
    verify,
)
from batchvault.errors import (  # noqa: E402
    BatchVaultError,
    ConfigError,
    EngineError,
    EngineNotFoundError,
    InputError,
    ManifestError,
    OptionsError,
    OutputExistsError,
    SignatureError,
    VolumeError,
)
from batchvault.models import (  # noqa: E402
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
