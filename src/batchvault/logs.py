import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from batchvault.security import redact, restrict_permissions

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LEVEL = "INFO"
LOGGER_NAME = "batchvault"
ENV_LOG_DIR = "BATCHVAULT_LOG_DIR"
KEEP_LOGS = 20


class RedactingFormatter(logging.Formatter):
    """Hides credentials embedded in URLs, including in tracebacks."""

    def format(self, record):
        return redact(super().format(record))


def default_log_dir() -> Path:
    """The conventional per-user log folder for the current OS."""
    override = os.environ.get(ENV_LOG_DIR)
    if override:
        return Path(override).expanduser()

    home = Path.home()

    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
        return base / "BatchVault" / "Logs"
    if sys.platform == "darwin":
        return home / "Library" / "Logs" / "BatchVault"

    state = os.environ.get("XDG_STATE_HOME")
    return (Path(state) if state else home / ".local" / "state") / "batchvault"


def prune_logs(directory: Path, keep: int = KEEP_LOGS) -> None:
    """Delete the oldest per-run log files beyond ``keep``."""
    try:
        logs = sorted(directory.glob("batchvault-*.log"))
        for old in logs[:-keep] if keep else logs:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def setup_logging(level=DEFAULT_LEVEL, log_file=None):
    """Log to a file for this run. Returns its path, or None if it can't be opened."""
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper()))
    close_logging()

    if log_file:
        path = Path(log_file).expanduser()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = default_log_dir() / f"batchvault-{stamp}-{os.getpid()}.log"

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError:
        logger.addHandler(logging.NullHandler())
        return None

    restrict_permissions(path)
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    logger.addHandler(handler)

    if not log_file:
        prune_logs(path.parent)
    return path.resolve()


def close_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    logger.addHandler(logging.NullHandler())
