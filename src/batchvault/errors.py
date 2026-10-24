class BatchVaultError(Exception):
    """Base class for all BatchVault errors."""


class EngineNotFoundError(BatchVaultError):
    """The requested compression engine is not available."""


class OptionsError(BatchVaultError, ValueError):
    """An option value is invalid."""


class ConfigError(BatchVaultError):
    """The configuration file is unreadable or contains invalid settings."""


class SignatureError(BatchVaultError):
    """A manifest signature is missing, invalid or cannot be created."""


class InputError(BatchVaultError):
    """An input file, list or path is missing or unusable."""


class EngineError(BatchVaultError):
    """A compression engine failed to run or reported an error."""


class OutputExistsError(BatchVaultError):
    """The destination already exists and replacing it was not requested."""


class ManifestError(BatchVaultError):
    """A manifest is missing, malformed or fails validation."""


class VolumeError(BatchVaultError):
    """Archive volumes are missing, incomplete or corrupt."""
