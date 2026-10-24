"""Webhook notifications. Used by the command-line interface only."""

from __future__ import annotations

import json
import platform
import urllib.error
import urllib.request
from urllib.parse import urlparse

from batchvault.errors import OptionsError
from batchvault.models import RunResult, Status

NOTIFY_ON = ("failure", "success", "always")
TIMEOUT_SECONDS = 10


def should_notify(when: str, result: RunResult) -> bool:
    if when == "always":
        return True
    ok = result.status == Status.SUCCESS
    return ok if when == "success" else not ok


def validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise OptionsError("The notification URL must start with http:// or https://")
    return url


def is_insecure(url: str) -> bool:
    """True for plain http:// to anything other than this machine."""
    parsed = urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    return parsed.scheme == "http" and not local


def build_payload(command: str, result: RunResult) -> dict:
    counts = ", ".join(
        f"{result.count(status)} {status.value}"
        for status in (Status.SUCCESS, Status.SKIPPED, Status.FAILED)
        if result.count(status)
    )
    text = (
        f"BatchVault {command} on {platform.node()}: "
        f"{result.status.value} ({counts or 'nothing to do'})"
    )
    # "text" is read by Slack and Teams, "content" by Discord.
    return {
        "text": text,
        "content": text,
        "command": command,
        "host": platform.node(),
        "result": result.to_dict(),
    }


def send(url: str, payload: dict) -> str | None:
    """POST the payload as JSON. Returns an error message, or None on success."""
    request = urllib.request.Request(
        validate_url(url),
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            return None
    except urllib.error.HTTPError as exc:
        return f"the server answered {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return str(getattr(exc, "reason", exc))
