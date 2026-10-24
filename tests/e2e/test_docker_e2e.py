"""End-to-end tests of the Docker images.

The images are built from the Dockerfile in this repository, so the tests cover
exactly what would be published. Set ``BATCHVAULT_E2E_IMAGE`` (and
``BATCHVAULT_E2E_IMAGE_7ZIP``) to test images that are already built or pulled.
The tests are skipped when Docker is not available.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from batchvault import __version__

pytestmark = pytest.mark.docker

REPO = Path(__file__).resolve().parents[2]


def _docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return probe.returncode == 0


if not _docker_available():
    pytest.skip("Docker is not available", allow_module_level=True)


def _build(tag: str, *build_args: str) -> str:
    command = ["docker", "build", "--quiet", "-t", tag]
    for arg in build_args:
        command += ["--build-arg", arg]
    process = subprocess.run(
        [*command, str(REPO)], capture_output=True, text=True, check=False
    )
    assert process.returncode == 0, process.stderr
    return tag


@pytest.fixture(scope="module")
def image() -> str:
    return os.environ.get("BATCHVAULT_E2E_IMAGE") or _build("batchvault-e2e:native")


@pytest.fixture(scope="module")
def image_7zip() -> str:
    return os.environ.get("BATCHVAULT_E2E_IMAGE_7ZIP") or _build(
        "batchvault-e2e:7zip", "WITH_7ZIP=true"
    )


@pytest.fixture
def docker(tmp_path):
    """Run a container with ``tmp_path`` mounted at /data."""

    def run(image, *args, env=None, entrypoint=None, mount=True):
        command = ["docker", "run", "--rm"]
        if mount:
            command += ["-v", f"{tmp_path}:/data"]
            if hasattr(os, "getuid"):  # keep file ownership on Linux hosts
                command += ["--user", f"{os.getuid()}:{os.getgid()}"]
        for key, value in (env or {}).items():
            command += ["-e", f"{key}={value}"]
        if entrypoint:
            command += ["--entrypoint", entrypoint]
        command += [image, *map(str, args)]
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", check=False
        )

    return run


def write_files(folder: Path) -> dict[str, bytes]:
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "report.bin": os.urandom(300_000),
        "notes.txt": b"hello world\n" * 2000,
        "ünï cödé.txt": "Unicode ✓ 日本語\n".encode() * 100,
    }
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return files


def test_version_and_help(docker, image):
    version = docker(image, "--version", mount=False)
    assert version.returncode == 0 and __version__ in version.stdout

    default = docker(image, mount=False)  # the image's default command is --help
    assert default.returncode == 0 and "usage: batchvault" in default.stdout
    assert docker(image, "pack", "--help", mount=False).returncode == 0
    assert docker(image, "nonsense", mount=False).returncode == 2


def test_runs_as_a_non_root_user_by_default(image):
    process = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "id", image, "-u"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.stdout.strip() == "1000"


def test_pack_and_unpack_in_a_container(docker, image, tmp_path):
    files = write_files(tmp_path / "input")

    packed = docker(
        image, "pack", *[f"input/{name}" for name in files], "-o", "archives",
        "--volume-size", "64k", "--engine", "native", "--verify", "--json",
        "--no-log",
    )  # fmt: skip
    assert packed.returncode == 0, packed.stderr
    result = json.loads(packed.stdout)
    assert result["status"] == "success" and result["engine"] == "native"
    assert (tmp_path / "archives" / "manifest.json").is_file()

    restored = docker(image, "unpack", "archives", "-o", "restored", "-q", "--no-log")
    assert restored.returncode == 0, restored.stderr
    for name, data in files.items():
        assert (tmp_path / "restored" / name).read_bytes() == data


def test_exit_code_reaches_the_host(docker, image, tmp_path):
    write_files(tmp_path / "input")
    args = ["pack", "input/notes.txt", "-o", "a", "--engine", "native", "-q"]
    assert docker(image, *args, "--no-log").returncode == 0
    assert docker(image, *args, "--no-log").returncode == 1  # skipped
    assert docker(image, "unpack", "missing", "-q", "--no-log").returncode == 2


def test_list_files_prints_json(docker, image, tmp_path):
    write_files(tmp_path / "input")
    listing = docker(image, "list", "files", "input", "--no-log")
    assert listing.returncode == 0
    assert len(json.loads(listing.stdout)) == 3


def test_encryption_is_available_in_the_image(docker, image, tmp_path):
    files = write_files(tmp_path / "input")
    env = {"BATCHVAULT_PASSWORD": "correct horse"}

    packed = docker(
        image, "pack", "input/notes.txt", "-o", "vault", "--engine", "native",
        "--encrypt", "-q", "--no-log", env=env,
    )  # fmt: skip
    assert packed.returncode == 0, packed.stderr

    assert docker(image, "verify", "vault", "-q", "--no-log").returncode == 1
    restored = docker(image, "unpack", "vault", "-o", "out", "-q", "--no-log", env=env)
    assert restored.returncode == 0, restored.stderr
    assert (tmp_path / "out" / "notes.txt").read_bytes() == files["notes.txt"]


def test_config_file_in_the_mounted_folder_is_used(docker, image, tmp_path):
    write_files(tmp_path / "input")
    (tmp_path / "batchvault.toml").write_text(
        '[pack]\nengine = "native"\nlevel = 8\n', encoding="utf-8"
    )

    packed = docker(image, "pack", "input/notes.txt", "-o", "a", "-q", "--no-log")
    assert packed.returncode == 0, packed.stderr

    manifest = json.loads((tmp_path / "a" / "manifest.json").read_text("utf-8"))
    assert manifest["entries"][0]["level"] == 8


def test_native_image_has_no_7zip(docker, image):
    report = json.loads(docker(image, "doctor", "--json", "--no-log").stdout)
    sevenzip = next(c for c in report if c["name"].startswith("7-Zip"))
    assert sevenzip["ok"] is False


def test_7zip_image_uses_7zip(docker, image_7zip, tmp_path):
    files = write_files(tmp_path / "input")

    report = json.loads(docker(image_7zip, "doctor", "--json", "--no-log").stdout)
    assert next(c for c in report if c["name"].startswith("7-Zip"))["ok"] is True

    packed = docker(
        image_7zip, "pack", *[f"input/{name}" for name in files], "-o", "a",
        "--volume-size", "64k", "--json", "--no-log",
    )  # fmt: skip
    assert packed.returncode == 0, packed.stderr
    assert json.loads(packed.stdout)["engine"] == "7zip"  # auto picks 7-Zip

    restored = docker(image_7zip, "unpack", "a", "-o", "out", "-q", "--no-log")
    assert restored.returncode == 0, restored.stderr
    for name, data in files.items():
        assert (tmp_path / "out" / name).read_bytes() == data
