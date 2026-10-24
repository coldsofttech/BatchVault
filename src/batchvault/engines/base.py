from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from batchvault.options import PackOptions


@dataclass(frozen=True)
class Capabilities:
    """What an engine supports, used to warn about unsupported settings."""

    levels: range
    threads_per_file: bool
    archive_suffix: str


class Engine(ABC):
    """Common interface implemented by every compression engine."""

    name: str
    capabilities: Capabilities

    @abstractmethod
    def is_available(self) -> bool:
        """True if the engine can run on this machine."""

    @abstractmethod
    def pack(self, source: Path, dest_dir: Path, options: PackOptions) -> list[Path]:
        """Archive ``source`` into volumes inside ``dest_dir``; return the volumes.

        Encrypts when ``options.password`` is set. Raises OutputExistsError if
        volumes already exist and ``options.force`` is false. Existing volumes
        are only removed once the new set is complete.
        """

    @abstractmethod
    def unpack(
        self,
        volume: Path,
        output_dir: Path,
        *,
        force: bool = False,
        password: str | None = None,
        max_bytes: int | None = None,
    ) -> list[Path]:
        """Restore the files in the volume set containing ``volume``.

        Raises OutputExistsError if a destination exists and ``force`` is false.
        With ``max_bytes``, an archive that would expand beyond that many bytes
        is refused before anything is written.
        """

    @abstractmethod
    def test(self, volume: Path, *, password: str | None = None) -> None:
        """Check the volume set's integrity; raise EngineError if it fails."""
