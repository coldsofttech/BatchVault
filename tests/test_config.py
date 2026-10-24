import json

import pytest

from batchvault.cli import main
from batchvault.config import find_config, load_config
from batchvault.errors import ConfigError


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello " * 200)
    (folder / "skip.log").write_text("log")
    return folder


def run(*args):
    return main([*args, "--no-log"])


def level_in(manifest):
    return json.loads(manifest.read_text(encoding="utf-8"))["entries"][0]["level"]


def test_find_config_walks_up(tmp_path, monkeypatch):
    config = tmp_path / "batchvault.toml"
    config.write_text("[pack]\nlevel = 7\n")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert find_config() == config.resolve()


def test_find_config_reads_pyproject_tool_section(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.other]\nx = 1\n")
    assert find_config(tmp_path) is None

    (tmp_path / "pyproject.toml").write_text("[tool.batchvault.pack]\nlevel = 2\n")
    found = find_config(tmp_path)
    assert found.name == "pyproject.toml"
    assert load_config(found) == {"pack": {"level": 2}}


def test_invalid_toml_and_unknown_section(tmp_path):
    bad = tmp_path / "batchvault.toml"
    bad.write_text("[pack\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(bad)

    bad.write_text("[nonsense]\nx = 1\n")
    with pytest.raises(ConfigError, match="unknown section"):
        load_config(bad)


def test_config_supplies_defaults_and_cli_wins(tmp_path, media):
    config = tmp_path / "bv.toml"
    config.write_text('[pack]\nlevel = 7\nengine = "native"\nvolume-size = "1k"\n')

    base = ["pack", str(media / "notes.txt"), "-q", "--config", str(config)]
    assert run(*base, "-o", str(tmp_path / "a")) == 0
    assert level_in(tmp_path / "a" / "manifest.json") == 7

    assert run(*base, "-o", str(tmp_path / "b"), "--level", "3") == 0
    assert level_in(tmp_path / "b" / "manifest.json") == 3


def test_defaults_table_and_list_options_combine(tmp_path, media, capsys):
    config = tmp_path / "bv.toml"
    config.write_text('[defaults]\nexclude = ["*.log"]\n')

    assert run("list", "files", str(media), "--config", str(config)) == 0
    names = [item["path"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
             for item in json.loads(capsys.readouterr().out)]  # fmt: skip
    assert names == ["notes.txt"]


def test_unknown_option_in_config_is_a_usage_error(tmp_path, media):
    config = tmp_path / "bv.toml"
    config.write_text("[pack]\nnot-an-option = 1\n")
    assert run("pack", str(media / "notes.txt"), "--config", str(config)) == 2


@pytest.mark.parametrize("line", ["level = 12", 'level = "high"', 'engine = "zip"'])
def test_invalid_value_in_config_is_a_usage_error(tmp_path, media, line):
    config = tmp_path / "bv.toml"
    config.write_text(f"[pack]\n{line}\n")
    assert run("pack", str(media / "notes.txt"), "--config", str(config)) == 2


def test_missing_explicit_config(tmp_path):
    assert run("doctor", "--config", str(tmp_path / "nope.toml")) == 2


# --- a config file found by searching is not fully trusted -------------------


RESTRICTED = [
    ("defaults", 'seven-zip = "./evil"'),
    ("pack", 'seven-zip = "./evil"'),
    ("pack", 'notify = "https://attacker.example/hook"'),
    ("notify", 'url = "https://attacker.example/hook"'),
    ("pack", "delete-source = true"),
    ("pack", "yes = true"),
    ("unpack", 'public-key = "attacker.pub"'),
    ("verify", 'public-key = "attacker.pub"'),
    ("pack", 'sign-key = "attacker.key"'),
    ("pack", 'password-file = "/etc/passwd"'),
    ("log", 'file = "/tmp/overwrite-me"'),
]


@pytest.mark.parametrize(("table", "line"), RESTRICTED)
def test_found_config_cannot_set_dangerous_options(tmp_path, monkeypatch, table, line):
    (tmp_path / "batchvault.toml").write_text(f"[{table}]\n{line}\n")
    monkeypatch.chdir(tmp_path)

    assert run("doctor", "-q") == 2


def test_error_explains_how_to_trust_the_file(tmp_path, monkeypatch, capsys):
    (tmp_path / "batchvault.toml").write_text('[pack]\nseven-zip = "./evil"\n')
    monkeypatch.chdir(tmp_path)

    run("doctor", "-q")
    message = " ".join(capsys.readouterr().err.split())
    assert "seven-zip" in message and "--config" in message


def test_pyproject_section_is_restricted_too(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.batchvault.pack]\ndelete-source = true\n'
    )
    monkeypatch.chdir(tmp_path)
    assert run("doctor", "-q") == 2


@pytest.mark.parametrize(("table", "line"), RESTRICTED[:3])
def test_the_same_file_is_accepted_when_named_explicitly(tmp_path, table, line):
    config = tmp_path / "trusted.toml"
    config.write_text(f"[{table}]\n{line}\n")
    assert run("doctor", "-q", "--config", str(config)) == 0


def test_environment_variable_counts_as_naming_the_file(tmp_path, monkeypatch):
    config = tmp_path / "trusted.toml"
    config.write_text('[defaults]\nseven-zip = "./whatever"\n')
    monkeypatch.setenv("BATCHVAULT_CONFIG", str(config))
    monkeypatch.chdir(tmp_path)
    assert run("doctor", "-q") == 0


def test_harmless_options_still_work_from_a_found_config(tmp_path, monkeypatch, media):
    (tmp_path / "batchvault.toml").write_text(
        '[pack]\nengine = "native"\nlevel = 4\nretries = 1\nupdate = true\n'
    )
    monkeypatch.chdir(tmp_path)

    code = run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"), "-q")
    assert code == 0
    assert level_in(tmp_path / "a" / "manifest.json") == 4
