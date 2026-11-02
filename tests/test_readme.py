import os
import re
import shlex
import shutil

import pytest

import batchvault as bv
from batchvault import signing
from batchvault.cli import main
from batchvault.readme import MARKER, README_NAME

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")

PASSWORD = "s3cret-pass-phrase"


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello " * 300, encoding="utf-8")
    (folder / "data.bin").write_bytes(os.urandom(3000))
    return folder


def pack(media, tmp_path, **options):
    return bv.pack(
        [media / "notes.txt", media / "data.bin"],
        tmp_path / "a",
        engine="native",
        volume_size="1k",
        **options,
    )


def readme_of(tmp_path):
    return (tmp_path / "a" / README_NAME).read_text(encoding="utf-8")


def commands_in(text, verb):
    """The ``batchvault <verb> ...`` lines in the README's code blocks."""
    return [
        line
        for block in re.findall(r"```\n(.*?)```", text, re.S)
        for line in block.splitlines()
        if line.startswith(f"batchvault {verb} ")
    ]


def run_readme_command(command, folder, monkeypatch):
    monkeypatch.chdir(folder)
    return main([*shlex.split(command)[1:], "--no-log", "-q"])


def test_plain_archive_readme(tmp_path, media):
    result = pack(media, tmp_path, include=["*.txt", "*.bin"], exclude=["*.tmp"])
    assert result.warnings == []

    text = readme_of(tmp_path)
    assert text.splitlines()[0] == MARKER
    assert f"BatchVault {bv.__version__}" in text
    assert "Files: 2" in text
    assert "`manifest.json`" in text
    assert "| Encrypted | no |" in text and "| Signed manifest | no |" in text
    assert "| Volume size | 1.0 KiB |" in text
    assert "`*.txt`, `*.bin`" in text and "`*.tmp`" in text
    assert f'pip install "batchvault=={bv.__version__}"' in text
    assert "pipx" in text and "uv tool install" in text and "3.11.4" in text
    assert "ghcr.io/coldsofttech/batchvault" in text and "BatchVault@v1" in text
    assert "--password-file" not in text.split("## Restore")[1].split("###")[0]
    assert "--public-key" not in text.split("## Restore")[1].split("###")[0]


def test_readme_commands_work_as_written(tmp_path, media, monkeypatch):
    pack(media, tmp_path)
    text = readme_of(tmp_path)
    folder = tmp_path / "a"

    assert run_readme_command(commands_in(text, "verify")[0], folder, monkeypatch) == 0

    folder_command = re.search(r"`(batchvault unpack \. -o restored)`", text)
    assert folder_command, text
    unpacks = [*commands_in(text, "unpack"), folder_command.group(1)]
    assert len(unpacks) == 2
    for command in unpacks:
        assert run_readme_command(command, folder, monkeypatch) == 0
        assert (folder / "restored" / "notes.txt").read_text() == "hello " * 300
        assert (folder / "restored" / "data.bin").read_bytes() == (
            media / "data.bin"
        ).read_bytes()
        shutil.rmtree(folder / "restored")


def test_single_volume_command_in_readme(tmp_path, media, monkeypatch):
    pack(media, tmp_path)
    text = readme_of(tmp_path)
    command = re.search(r"`(batchvault unpack \S+\.001 -o restored)`", text)
    assert command, text

    folder = tmp_path / "a"
    assert run_readme_command(command.group(1), folder, monkeypatch) == 0
    assert any((folder / "restored").iterdir())


@needs_crypto
def test_encrypted_readme_has_no_secret(tmp_path, media, monkeypatch):
    pack(media, tmp_path, password=PASSWORD)
    text = readme_of(tmp_path)

    assert PASSWORD not in text
    assert "| Encrypted | yes |" in text
    assert "This archive is encrypted" in text
    assert "batchvault[crypto]==" in text
    assert "BATCHVAULT_PASSWORD" in text

    (tmp_path / "a" / "password.txt").write_text(PASSWORD, encoding="utf-8")
    folder = tmp_path / "a"
    for verb in ("verify", "unpack"):
        command = commands_in(text, verb)[0]
        assert "--password-file password.txt" in command
        assert run_readme_command(command, folder, monkeypatch) == 0
    assert (folder / "restored" / "notes.txt").exists()


@needs_crypto
def test_signed_readme(tmp_path, media, monkeypatch):
    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    pack(media, tmp_path, sign_key=private)
    text = readme_of(tmp_path)

    assert "| Signed manifest | yes |" in text
    assert "manifest.json.sig" in text
    assert "BEGIN" not in text  # no key material
    assert str(private) not in text

    folder = tmp_path / "a"
    (folder / "batchvault.pub").write_bytes(public.read_bytes())
    for verb in ("verify", "unpack"):
        command = commands_in(text, verb)[0]
        assert "--public-key batchvault.pub" in command
        assert run_readme_command(command, folder, monkeypatch) == 0


def test_no_readme_option(tmp_path, media):
    pack(media, tmp_path, readme=False)
    assert not (tmp_path / "a" / README_NAME).exists()


def test_no_readme_cli_flag(tmp_path, media):
    args = ["pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
            "--engine", "native", "-q", "--no-log"]  # fmt: skip
    assert main([*args, "--no-readme"]) == 0
    assert not (tmp_path / "a" / README_NAME).exists()
    assert main([*args, "--force"]) == 0
    assert (tmp_path / "a" / README_NAME).exists()


def test_dry_run_writes_nothing(tmp_path, media):
    pack(media, tmp_path, dry_run=True)
    assert not (tmp_path / "a").exists()


def test_readme_is_not_an_archive_volume(tmp_path, media):
    pack(media, tmp_path)
    assert bv.verify(tmp_path / "a").status == bv.Status.SUCCESS
    assert {e.name for e in bv.inspect(tmp_path / "a")} == {"notes.txt", "data.bin"}


def test_update_refreshes_readme_and_keeps_working(tmp_path, media):
    pack(media, tmp_path)
    first = readme_of(tmp_path)

    again = pack(media, tmp_path, update=True)
    assert again.warnings == []
    assert "Files: 2" in readme_of(tmp_path)

    (media / "notes.txt").write_text("changed " * 200, encoding="utf-8")
    pack(media, tmp_path, update=True)
    assert "Update mode (`--update`) | yes" in readme_of(tmp_path)
    assert readme_of(tmp_path) != first
    assert bv.verify(tmp_path / "a").status == bv.Status.SUCCESS


def test_own_readme_is_never_overwritten(tmp_path, media):
    out = tmp_path / "a"
    out.mkdir()
    (out / README_NAME).write_text("my own notes\n", encoding="utf-8")

    result = pack(media, tmp_path)
    assert (out / README_NAME).read_text() == "my own notes\n"
    assert any(README_NAME in w for w in result.warnings)


def test_source_with_the_readme_name_is_left_alone(tmp_path, media):
    (media / README_NAME).write_text("a source file\n", encoding="utf-8")
    result = bv.pack(
        [media / README_NAME],
        tmp_path / "a",
        engine="native",
        allow_archives=True,
    )
    assert result.status == bv.Status.SUCCESS
    assert (tmp_path / "a" / README_NAME).is_dir()  # the archive of that source
    assert any(README_NAME in w for w in result.warnings)

    restored = tmp_path / "r"
    bv.unpack(tmp_path / "a", restored)
    assert (restored / README_NAME).read_text() == "a source file\n"


def test_packing_the_archive_folder_skips_the_readme(tmp_path, media):
    pack(media, tmp_path)
    with pytest.raises(bv.InputError):
        bv.pack([tmp_path / "a" / README_NAME], tmp_path / "b", engine="native")
