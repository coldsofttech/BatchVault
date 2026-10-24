import os
import re
from pathlib import Path

# Credentials embedded in a URL: scheme://user:password@host
_URL_CREDENTIALS = re.compile(r"(?<=://)[^/\s:@]+(:[^/\s@]*)?@")


def redact(text: str) -> str:
    """Hide credentials embedded in URLs."""
    return _URL_CREDENTIALS.sub("***@", text)


SECRET_OPTIONS = ("--notify",)


def redact_arguments(argv: list[str]) -> list[str]:
    """Hide the values of options that carry secrets (webhook URLs contain one)."""
    hidden = []
    mask_next = False
    for arg in argv:
        if mask_next:
            hidden.append("***")
            mask_next = False
        elif arg in SECRET_OPTIONS:
            hidden.append(arg)
            mask_next = True
        elif any(arg.startswith(f"{option}=") for option in SECRET_OPTIONS):
            hidden.append(arg.split("=", 1)[0] + "=***")
        else:
            hidden.append(redact(arg))
    return hidden


def restrict_permissions(path: Path) -> None:
    """Make a file readable by its owner only (no effect on Windows)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
