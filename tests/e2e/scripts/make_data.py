"""Create a small tree of test files: binary, text, Unicode names, spaces, empty.

Used by the end-to-end tests and by the GitHub Action workflow:

    python tests/e2e/scripts/make_data.py data
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def make_data(root: Path) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)

    contents = {
        "report.bin": os.urandom(2 * 1024 * 1024),
        "notes.txt": ("hello world\n" * 5000).encode("utf-8"),
        "ünï cödé.txt": ("Unicode ✓ 日本語\n" * 1000).encode("utf-8"),
        "empty.dat": b"",
        "sub dir/nested file.csv": b"a,b,c\n" * 2000,
    }

    paths = []
    for name, data in contents.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths.append(path)
    return paths


if __name__ == "__main__":
    for created in make_data(Path(sys.argv[1])):
        print(created)
