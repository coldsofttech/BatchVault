"""Shared setup for the end-to-end tests.

Everything under tests/e2e is marked ``e2e`` automatically, so the normal unit
test run (``-m "not integration and not e2e"``) skips it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
REPO = HERE.parent.parent

sys.path.insert(0, str(HERE / "scripts"))

from make_data import make_data  # noqa: E402


def pytest_collection_modifyitems(items):
    for item in items:
        if HERE in Path(str(item.path)).parents:
            item.add_marker(pytest.mark.e2e)


@pytest.fixture
def repo_root() -> Path:
    return REPO


@pytest.fixture
def data_dir(tmp_path) -> Path:
    """A tree of test files; the folder is returned."""
    root = tmp_path / "data"
    make_data(root)
    return root
