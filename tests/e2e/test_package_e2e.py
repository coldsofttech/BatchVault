"""End-to-end tests of the built package, installed into a clean virtual env.

These catch problems the normal tests cannot: files missing from the wheel,
a broken entry point, an undeclared dependency, or the optional ``crypto``
extra not behaving as documented. They need network access (pip) and take a
minute or so.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from batchvault import __version__

pytestmark = pytest.mark.package

REPO = Path(__file__).resolve().parents[2]


def run(command, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(part) for part in command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        **kwargs,
    )


def checked(command, **kwargs) -> subprocess.CompletedProcess:
    process = run(command, **kwargs)
    assert process.returncode == 0, f"{command}\n{process.stdout}\n{process.stderr}"
    return process


def venv_python(venv: Path) -> Path:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    return venv / folder / ("python.exe" if sys.platform == "win32" else "python")


def venv_script(venv: Path, name: str) -> Path:
    folder = "Scripts" if sys.platform == "win32" else "bin"
    return venv / folder / (f"{name}.exe" if sys.platform == "win32" else name)


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("wheel")
    uv = shutil.which("uv")
    if uv:
        checked([uv, "build", "--wheel", "--out-dir", out], cwd=REPO)
    else:
        checked(
            [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", out],
            cwd=REPO,
        )
    wheels = list(out.glob("batchvault-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def make_venv(tmp_path_factory, wheel: Path, extra: str = "") -> Path:
    venv = tmp_path_factory.mktemp("venv")
    uv = shutil.which("uv")

    # uv is quicker and more reliable than the pip bundled with a new venv.
    if uv:
        checked([uv, "venv", "--quiet", "--python", sys.executable, venv])
        install = [uv, "pip", "install", "--quiet", "--python", venv_python(venv)]
    else:
        checked([sys.executable, "-m", "venv", venv])
        install = [
            venv_python(venv), "-m", "pip", "install", "--quiet",
            "--disable-pip-version-check",
        ]  # fmt: skip
    command = [*install, f"{wheel}{extra}"]

    # Downloading dependencies can fail for reasons that have nothing to do with
    # the package (a flaky connection or proxy), so try a few times.
    for attempt in range(1, 3):
        if run(command).returncode == 0:
            return venv
        time.sleep(3 * attempt)
    checked(command)  # last try; shows the real error if it still fails
    return venv


@pytest.fixture(scope="module")
def plain_venv(tmp_path_factory, wheel) -> Path:
    return make_venv(tmp_path_factory, wheel)


@pytest.fixture(scope="module")
def crypto_venv(tmp_path_factory, wheel) -> Path:
    return make_venv(tmp_path_factory, wheel, "[crypto]")


def test_wheel_contents(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "batchvault/py.typed" in names
    for module in ("api", "cli", "ui", "engines/native", "engines/sevenzip"):
        assert f"batchvault/{module}.py" in names
    assert not [n for n in names if n.startswith(("tests/", ".temp/", "docs/"))]


def test_metadata_and_entry_point(plain_venv):
    script = (
        "import importlib.metadata as md, json\n"
        "dist = md.distribution('batchvault')\n"
        "print(json.dumps({\n"
        "    'version': dist.version,\n"
        "    'python': dist.metadata['Requires-Python'],\n"
        "    'requires': dist.requires,\n"
        "    'scripts': [e.name for e in dist.entry_points\n"
        "                if e.group == 'console_scripts'],\n"
        "}))\n"
    )
    info = json.loads(checked([venv_python(plain_venv), "-c", script]).stdout)

    assert info["version"] == __version__
    assert info["python"] == ">=3.11.4"
    assert info["scripts"] == ["batchvault"]
    requires = " ".join(info["requires"])
    assert "rich" in requires and "pathspec" in requires

    # cryptography is only required through the optional "crypto" extra.
    crypto = [r for r in info["requires"] if r.startswith("cryptography")]
    assert len(crypto) == 1 and "extra" in crypto[0] and "crypto" in crypto[0]


def test_installed_command_and_module(plain_venv):
    command = checked([venv_script(plain_venv, "batchvault"), "--version"])
    assert __version__ in command.stdout

    module = checked([venv_python(plain_venv), "-m", "batchvault", "--version"])
    assert module.stdout == command.stdout


API_SCRIPT = """
import json, os, sys
from pathlib import Path
import batchvault as bv

work = Path(sys.argv[1])
source = work / "données 1.bin"
source.write_bytes(os.urandom(300_000))  # incompressible, so it needs many volumes

packed = bv.pack([source], work / "a", engine="native", volume_size="64k")
unpacked = bv.unpack(work / "a", work / "out")
checked = bv.verify(work / "a", against=work)

print(json.dumps({
    "packed": packed.status.value,
    "unpacked": unpacked.status.value,
    "verified": checked.status.value,
    "same": (work / "out" / source.name).read_bytes() == source.read_bytes(),
    "volumes": len(packed.entries[0].volumes),
    "rich_imported": "rich" in sys.modules,
}))
"""


def test_python_api_in_a_clean_environment(plain_venv, tmp_path):
    process = checked([venv_python(plain_venv), "-c", API_SCRIPT, tmp_path])
    result = json.loads(process.stdout)

    assert result["packed"] == result["unpacked"] == result["verified"] == "success"
    assert result["same"] and result["volumes"] > 1
    # The library must not pull in the terminal UI.
    assert result["rich_imported"] is False


def test_missing_crypto_extra_gives_a_clear_message(plain_venv, tmp_path):
    source = tmp_path / "secret.txt"
    source.write_text("secret")

    script = (
        "import sys, batchvault as bv\n"
        "try:\n"
        "    bv.pack([sys.argv[1]], sys.argv[2], engine='native', password='x')\n"
        "except bv.EngineNotFoundError as exc:\n"
        "    print(exc)\n"
    )
    message = checked(
        [venv_python(plain_venv), "-c", script, source, tmp_path / "a"]
    ).stdout
    assert "batchvault[crypto]" in message
    assert not (tmp_path / "a" / "secret.txt").exists()

    command = [
        venv_script(plain_venv, "batchvault"),
        "pack",
        source,
        "-o",
        tmp_path / "b",
        "--engine",
        "native",
        "--encrypt",
        "--no-log",
    ]
    cli = run(command, env={**os.environ, "BATCHVAULT_PASSWORD": "x"})
    assert cli.returncode == 2
    assert "batchvault[crypto]" in cli.stderr


def test_crypto_extra_enables_encryption_and_signing(crypto_venv, tmp_path):
    script = (
        "import sys, json\n"
        "from pathlib import Path\n"
        "import batchvault as bv\n"
        "from batchvault import signing\n"
        "work = Path(sys.argv[1])\n"
        "source = work / 'notes.txt'\n"
        "source.write_text('hello ' * 1000)\n"
        "signing.generate_keypair(work / 'k.key', work / 'k.pub')\n"
        "bv.pack([source], work / 'a', engine='native', password='pw',\n"
        "        sign_key=work / 'k.key')\n"
        "good = bv.unpack(work / 'a', work / 'out', password='pw',\n"
        "                 public_key=work / 'k.pub')\n"
        "bad = bv.unpack(work / 'a', work / 'out2', password='nope')\n"
        "print(json.dumps({'good': good.status.value, 'bad': bad.status.value,\n"
        "                  'same': (work / 'out' / 'notes.txt').read_text()\n"
        "                          == source.read_text()}))\n"
    )
    result = json.loads(
        checked([venv_python(crypto_venv), "-c", script, tmp_path]).stdout
    )
    assert result == {"good": "success", "bad": "failed", "same": True}
