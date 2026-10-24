"""Work out which archives a path given to unpack, verify or list refers to."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from batchvault.errors import InputError, ManifestError
from batchvault.inputs import read_list_entries
from batchvault.manifest import MANIFEST_NAME, Manifest, ManifestEntry
from batchvault.volumes import split_volume_name


@dataclass
class Target:
    """One archive to process. ``entry`` is None when no manifest is available."""

    volume: Path  # first volume (or the given one when there is no manifest)
    entry: ManifestEntry | None = None
    base_dir: Path | None = None  # folder the entry's volume paths are relative to
    manifest: Path | None = None  # the manifest file the entry came from

    @property
    def name(self) -> str:
        if self.entry:
            return self.entry.name
        base = split_volume_name(self.volume.name)[0]
        return base.rsplit(".", 1)[0]


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _find_manifest(*folders: Path) -> Path | None:
    for folder in folders:
        candidate = folder / MANIFEST_NAME
        if candidate.is_file():
            return candidate
    return None


Loader = Callable[[Path], Manifest]


def _manifest_targets(
    manifest_path: Path, under: Path | None = None, load: Loader = Manifest.load
) -> list[Target]:
    manifest = load(manifest_path)
    base = manifest_path.parent

    targets = []
    for entry in manifest.entries.values():
        first = base / entry.volumes[0].file
        if under is None or _is_under(first, under):
            targets.append(Target(first, entry, base, manifest_path))
    return targets


def _is_under(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _volume_target(volume: Path, load: Loader = Manifest.load) -> Target:
    """Target for one volume path, using a nearby manifest when it knows it."""
    if split_volume_name(volume.name) is None:
        raise InputError(f"Not an archive volume: {volume}")
    if not volume.is_file():
        raise InputError(f"Archive volume not found: {volume}")

    manifest_path = _find_manifest(volume.parent, volume.parent.parent)
    if manifest_path:
        base = manifest_path.parent
        for entry in load(manifest_path).entries.values():
            if any(_same(base / v.file, volume) for v in entry.volumes):
                return Target(base / entry.volumes[0].file, entry, base, manifest_path)

    return Target(volume)


def resolve_targets(
    source: str | Path, load: Loader = Manifest.load
) -> list[Target]:
    """Archives referred to by a manifest, folder, volume file or list file.

    Every manifest is read through ``load``, so a caller can verify a manifest's
    signature on the very bytes that are then parsed.
    """
    path = Path(source).expanduser()
    if not path.exists():
        raise InputError(f"Path not found: {path}")

    if path.is_dir():
        manifest_path = _find_manifest(path, path.parent)
        if manifest_path:
            under = None if manifest_path.parent == path else path
            targets = _manifest_targets(manifest_path, under, load)
        else:
            targets = [
                Target(found)
                for found in sorted(path.rglob("*"))
                if found.is_file()
                and (parsed := split_volume_name(found.name))
                and parsed[1] == 1
            ]
        if not targets:
            raise InputError(f"No archives found in {path}")
        return targets

    if split_volume_name(path.name):
        return [_volume_target(path, load)]

    if path.name == MANIFEST_NAME or path.suffix.lower() == ".json":
        try:
            return _manifest_targets(path, None, load)
        except ManifestError:
            # A JSON list of volume paths is also accepted; fall through.
            if path.name == MANIFEST_NAME:
                raise

    targets = []
    for label, entry in read_list_entries(path):
        volume = Path(entry).expanduser()
        if not volume.is_absolute():
            volume = path.parent / volume
        try:
            targets.append(_volume_target(volume, load))
        except InputError as exc:
            raise InputError(f"{label}: {exc}") from exc

    if not targets:
        raise InputError(f"No archive paths found in {path}")
    return targets
