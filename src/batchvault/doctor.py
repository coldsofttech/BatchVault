"""Checks that BatchVault can run on this machine."""

from __future__ import annotations

import os
import platform
import sys
import tempfile
from dataclasses import dataclass

from batchvault.engines.sevenzip import find_7zip
from batchvault.errors import EngineNotFoundError
from batchvault.logs import default_log_dir


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = False  # a failed required check makes doctor exit non-zero


def _module(name: str, label: str, required: bool = False) -> Check:
    try:
        __import__(name)
    except ImportError:
        return Check(label, False, "not available in this Python build", required)
    return Check(label, True, "available", required)


def _log_dir_check() -> Check:
    folder = default_log_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder):
            pass
    except OSError as exc:
        return Check("Log folder", False, f"{folder} is not writable: {exc}")
    return Check("Log folder", True, str(folder))


def run_checks(seven_zip: str | None = None) -> list[Check]:
    checks = [
        Check(
            "Python",
            sys.version_info >= (3, 11, 4),  # safe tar extraction filters
            f"{platform.python_version()} on {platform.system()}",
            required=True,
        ),
        _module("zlib", "zlib (gzip, store)", required=True),
        _module("lzma", "lzma (xz format)"),
        _module("bz2", "bz2 (bzip2 format)"),
    ]

    try:
        checks.append(Check("7-Zip (optional)", True, str(find_7zip(seven_zip))))
    except EngineNotFoundError as exc:
        checks.append(Check("7-Zip (optional)", False, str(exc)))

    checks.append(_log_dir_check())
    checks.append(Check("CPU threads", True, str(os.cpu_count() or 1)))
    return checks


def passed(checks: list[Check]) -> bool:
    return all(check.ok for check in checks if check.required)
