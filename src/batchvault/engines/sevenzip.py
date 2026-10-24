from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from batchvault.engines.base import Capabilities, Engine
from batchvault.errors import EngineError, EngineNotFoundError, OutputExistsError
from batchvault.options import PackOptions
from batchvault.volumes import existing_volumes, find_volumes, install_volumes

ENV_VAR = "BATCHVAULT_7Z"
COMMANDS = ("7zz", "7z", "7za", "7zr")
WINDOWS_PATHS = (
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
)
OUTPUT_TAIL_LINES = 15
LIMIT_POLL_SECONDS = 0.05


def _search_path(command: str) -> Path | None:
    """Find ``command`` in the folders listed in ``PATH``.

    Unlike ``shutil.which``, this never looks in the current folder (Windows does
    that first) and ignores empty and relative ``PATH`` entries, so a program
    planted in the folder you happen to be working in is never run.
    """
    cwd = Path.cwd().resolve()
    if sys.platform == "win32":
        extensions = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        names = [command + extension.lower() for extension in extensions if extension]
    else:
        names = [command]

    for entry in os.environ.get("PATH", "").split(os.pathsep):
        folder = Path(entry) if entry else None
        if folder is None or not folder.is_absolute():
            continue
        try:
            if folder.resolve() == cwd:
                continue
        except OSError:
            continue

        for name in names:
            candidate = folder / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    return None


def find_7zip(explicit: str | Path | None = None) -> Path:
    """Locate a 7-Zip command-line executable.

    Order: explicit path, ``BATCHVAULT_7Z``, ``PATH`` (never the current folder),
    then well-known Windows install folders.
    """
    configured = explicit or os.environ.get(ENV_VAR)
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return path
        raise EngineNotFoundError(f"7-Zip executable not found: {configured}")

    for command in COMMANDS:
        found = _search_path(command)
        if found:
            return found

    if sys.platform == "win32":
        for candidate in WINDOWS_PATHS:
            if candidate.is_file():
                return candidate

    raise EngineNotFoundError(
        "Could not find 7-Zip. Install it from https://www.7-zip.org/ (7zz on "
        f"Linux and macOS), or set the path with --7z or {ENV_VAR}."
    )


def build_pack_command(
    executable: Path, archive: Path, source: Path, options: PackOptions
) -> list[str]:
    command = [
        str(executable),
        "a",
        "-t7z",
        f"-mx={options.level}",
        "-m0=LZMA2",
        f"-mmt={max(options.jobs, 1)}",
        f"-v{options.volume_size}b",
        "-y",
        # A file name containing * or ? is a name, not a pattern; without this
        # 7-Zip would archive every neighbour it matches.
        "-spd",
    ]
    if options.password:
        # 7-Zip only takes the password on its command line, where other users
        # of this machine could see it in the process list while it runs.
        command += [f"-p{options.password}", "-mhe=on"]
    return [*command, str(archive), str(source)]


def _password_args(password: str | None) -> list[str]:
    return [f"-p{password}"] if password else []


def build_unpack_command(
    executable: Path, first_volume: Path, target: Path, password: str | None = None
):
    return [
        str(executable),
        "e",
        str(first_volume),
        f"-o{target}",
        "-y",
        "-spd",
        *_password_args(password),
    ]


def build_test_command(
    executable: Path, first_volume: Path, password: str | None = None
) -> list[str]:
    return [
        str(executable), "t", str(first_volume), "-spd", *_password_args(password)
    ]  # fmt: skip


def folder_size(folder: Path) -> int:
    """Total size of the files below ``folder`` (files that vanish are ignored)."""
    total = 0
    for root, _, names in os.walk(folder):
        for name in names:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


class SevenZipEngine(Engine):
    name = "7zip"
    capabilities = Capabilities(
        levels=range(0, 10), threads_per_file=True, archive_suffix=".7z"
    )

    def __init__(self, executable: str | Path | None = None):
        self._explicit = executable
        self._executable: Path | None = None

    @property
    def executable(self) -> Path:
        if self._executable is None:
            self._executable = find_7zip(self._explicit)
        return self._executable

    def is_available(self) -> bool:
        try:
            find_7zip(self._explicit)
        except EngineNotFoundError:
            return False
        return True

    @staticmethod
    def _failure(code: int, output: str) -> EngineError:
        tail = "\n".join(output.strip().splitlines()[-OUTPUT_TAIL_LINES:])
        return EngineError(f"7-Zip failed (exit code {code}).\n{tail}".rstrip())

    def _run(self, command: list[str]) -> str:
        """Run 7-Zip; return its standard output, raise EngineError on failure."""
        result = subprocess.run(
            command,
            capture_output=True,
            stdin=subprocess.DEVNULL,  # never wait for a password prompt
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise self._failure(result.returncode, result.stderr or result.stdout)
        return result.stdout

    def _run_limited(self, command: list[str], watch: Path, max_bytes: int) -> str:
        """Like ``_run``, but stop the program if ``watch`` grows past ``max_bytes``.

        7-Zip writes as it extracts, so a decompression bomb is cut off while it
        is still small instead of filling the disk. Output goes to a temporary
        file so a full pipe can never stall the program.
        """
        too_big = EngineError(
            f"The archive would expand beyond the allowed size ({max_bytes} bytes); "
            "extraction was stopped."
        )

        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                while True:
                    try:
                        code = process.wait(timeout=LIMIT_POLL_SECONDS)
                        break
                    except subprocess.TimeoutExpired:
                        if folder_size(watch) > max_bytes:
                            raise too_big from None
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()

            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")

        if folder_size(watch) > max_bytes:  # it finished between two checks
            raise too_big
        if code != 0:
            raise self._failure(code, text)
        return text

    def pack(self, source: Path, dest_dir: Path, options: PackOptions) -> list[Path]:
        if not source.is_file():
            raise FileNotFoundError(f"Source file not found: {source}")

        base = f"{source.name}{self.capabilities.archive_suffix}"
        existing = existing_volumes(dest_dir, base)
        if existing and not options.force:
            raise OutputExistsError(
                f"Archive volumes already exist in {dest_dir}. Use --force to replace."
            )

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Build in a temporary folder so a failed run never damages what exists.
        with tempfile.TemporaryDirectory(dir=dest_dir, prefix=".batchvault-") as tmp:
            staging = Path(tmp)
            self._run(
                build_pack_command(self.executable, staging / base, source, options)
            )

            built = existing_volumes(staging, base)
            if not built:
                raise EngineError(f"7-Zip produced no volumes for {source}")

            return install_volumes(built, dest_dir, existing)

    def unpack(
        self,
        volume: Path,
        output_dir: Path,
        *,
        force: bool = False,
        password: str | None = None,
        max_bytes: int | None = None,
    ) -> list[Path]:
        first = find_volumes(volume)[0]
        output_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=output_dir, prefix=".batchvault-") as tmp:
            staging = Path(tmp)
            command = build_unpack_command(self.executable, first, staging, password)
            if max_bytes is None:
                self._run(command)
            else:
                self._run_limited(command, staging, max_bytes)

            # Never accept links or other special files from an archive.
            if any(p.is_symlink() or not p.is_file() for p in staging.iterdir()):
                raise EngineError(
                    f"{first.name} contains a link or special file, which is refused."
                )

            extracted = sorted(staging.iterdir())
            if not extracted:
                raise EngineError(f"7-Zip extracted no files from {first}")

            conflicts = [output_dir / p.name for p in extracted]
            conflicts = [path for path in conflicts if path.exists()]
            if conflicts and not force:
                names = ", ".join(path.name for path in conflicts)
                raise OutputExistsError(
                    f"Destination already exists: {names}. Use --force to overwrite."
                )

            restored = []
            for item in extracted:
                target = output_dir / item.name
                os.replace(item, target)
                restored.append(target)

        return restored

    def test(self, volume: Path, *, password: str | None = None) -> None:
        first = find_volumes(volume)[0]
        self._run(build_test_command(self.executable, first, password))
