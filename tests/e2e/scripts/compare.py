"""Check that restored files are byte-for-byte identical to the originals.

    python tests/e2e/scripts/compare.py SOURCE_TREE RESTORED_FOLDER [NAME ...]

Every file under SOURCE_TREE (or only the NAMEs given) must exist in
RESTORED_FOLDER at the same relative path with identical content. Exits 1 on any
difference.
"""

from __future__ import annotations

import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    source, restored = Path(argv[0]), Path(argv[1])
    wanted = set(argv[2:])

    originals = [
        path
        for path in sorted(source.rglob("*"))
        if path.is_file() and (not wanted or path.name in wanted)
    ]
    if not originals:
        print(f"No files to compare under {source}")
        return 1

    problems = 0
    for original in originals:
        # Folders are kept, so a file comes back at the same path below the folder.
        relative = original.relative_to(source)
        copy = restored / relative
        if not copy.is_file():
            print(f"MISSING  {copy}")
            problems += 1
        elif copy.read_bytes() != original.read_bytes():
            print(f"DIFFERS  {copy}")
            problems += 1
        else:
            print(f"OK       {relative.as_posix()}")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
