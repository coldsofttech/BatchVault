"""Project configuration from ``batchvault.toml`` or ``[tool.batchvault]``.

Layout (keys use the same names as the command-line options)::

    [defaults]            # applies to every command that has the option
    jobs = 4

    [log]
    level = "DEBUG"       # --log-level
    file = "run.log"      # --log-file

    [notify]
    url = "https://hooks.example.com/..."   # --notify
    on = "failure"                          # --notify-on

    [pack]                # per command: pack, unpack, verify, inspect
    level = 7
    volume-size = "100m"
    include = ["*.mp4"]

Command-line options override the file. List options (``include``,
``exclude``) from the file and the command line are combined.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from batchvault.errors import ConfigError

CONFIG_FILE = "batchvault.toml"
PYPROJECT = "pyproject.toml"

COMMAND_TABLES = ("pack", "unpack", "verify", "inspect")
# Tables that map their own key names onto command-line options.
SECTION_KEYS = {
    "log": {"level": "log_level", "file": "log_file"},
    "notify": {"url": "notify", "on": "notify_on"},
}
KNOWN_TABLES = {"defaults", *COMMAND_TABLES, *SECTION_KEYS}

# Options that run a program, send data out, choose which keys to trust, read a
# password from somewhere or delete files. A config file found automatically in
# the current or a parent folder may belong to someone else (a cloned
# repository, a downloaded folder), so it may not set these. A file named with
# --config or BATCHVAULT_CONFIG is trusted.
RESTRICTED_OPTIONS = frozenset(
    {
        "seven_zip",
        "notify",
        "delete_source",
        "yes",
        "public_key",
        "sign_key",
        "password_file",
        "log_file",
    }
)


def _tool_section(pyproject: Path) -> dict | None:
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    section = data.get("tool", {}).get("batchvault")
    return section if isinstance(section, dict) else None


def find_config(start: Path | None = None) -> Path | None:
    """Look for a config file in ``start`` and then each parent folder."""
    folder = (start or Path.cwd()).resolve()
    for candidate in (folder, *folder.parents):
        if (candidate / CONFIG_FILE).is_file():
            return candidate / CONFIG_FILE
        pyproject = candidate / PYPROJECT
        if pyproject.is_file() and _tool_section(pyproject) is not None:
            return pyproject
    return None


def load_config(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid TOML: {exc}") from exc

    if path.name == PYPROJECT:
        data = data.get("tool", {}).get("batchvault", {})

    unknown = sorted(set(data) - KNOWN_TABLES)
    if unknown:
        raise ConfigError(
            f"{path}: unknown section(s): {', '.join(unknown)}. "
            f"Known: {', '.join(sorted(KNOWN_TABLES))}."
        )
    for name, table in data.items():
        if not isinstance(table, dict):
            raise ConfigError(f"{path}: [{name}] must be a table")
    return data


def reject_restricted(config: dict, path: Path) -> None:
    """Refuse restricted options in a config file that was found automatically."""
    for table_name, table in config.items():
        mapping = SECTION_KEYS.get(table_name)
        for key in table:
            dest = mapping.get(key, key) if mapping else key.replace("-", "_")
            if dest in RESTRICTED_OPTIONS:
                raise ConfigError(
                    f"{path} sets '{key}' in [{table_name}]. A config file found "
                    "automatically cannot set options that run programs, send data, "
                    "choose trusted keys, read passwords or delete files. If you "
                    f"trust this file, run with: --config {path}"
                )


def _check_value(dest: str, value, action, where: str) -> None:
    if action.choices is not None and value not in action.choices:
        choices = ", ".join(str(choice) for choice in action.choices)
        raise ConfigError(f"{where}: '{dest}' must be one of: {choices}")

    if action.nargs == 0:  # on/off flag
        if not isinstance(value, bool):
            raise ConfigError(f"{where}: '{dest}' must be true or false")
    elif action.type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{where}: '{dest}' must be a whole number")
    elif action.default is not None and isinstance(action.default, list):
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where}: '{dest}' must be a list of strings")
    elif not isinstance(value, str | int | float) or isinstance(value, bool):
        raise ConfigError(f"{where}: '{dest}' has an unsupported value")


def defaults_for(config: dict, command: str, actions: dict) -> dict:
    """Option defaults for ``command`` (``actions`` maps option name to Action)."""
    merged: dict = {}

    for key, value in config.get("defaults", {}).items():
        dest = key.replace("-", "_")
        if dest in actions:
            _check_value(dest, value, actions[dest], "[defaults]")
            merged[dest] = value

    for section, mapping in SECTION_KEYS.items():
        for key, value in config.get(section, {}).items():
            dest = mapping.get(key)
            if dest is None:
                raise ConfigError(
                    f"Unknown key '{key}' in [{section}]. "
                    f"Known: {', '.join(mapping)}."
                )
            if dest in actions:
                _check_value(dest, value, actions[dest], f"[{section}]")
                merged[dest] = value

    for key, value in config.get(command, {}).items():
        dest = key.replace("-", "_")
        if dest not in actions:
            raise ConfigError(f"Unknown option '{key}' in [{command}].")
        _check_value(dest, value, actions[dest], f"[{command}]")
        merged[dest] = value

    return merged
