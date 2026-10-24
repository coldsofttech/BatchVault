import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from batchvault.cli import main

try:
    import cryptography  # noqa: F401

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

needs_crypto = pytest.mark.skipif(not HAS_CRYPTO, reason="cryptography not installed")


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "notes.txt").write_text("hello " * 300, encoding="utf-8")
    (folder / "data.bin").write_bytes(os.urandom(3000))
    return folder


def run(*args):
    return main([*args, "--no-log"])


def pack_args(media, tmp_path, *extra):
    return ["pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
            "--engine", "native", "-q", *extra]  # fmt: skip


def test_json_events_stream(tmp_path, media, capsys):
    code = run("pack", str(media / "notes.txt"), "-o", str(tmp_path / "a"),
               "--engine", "native", "--json-events")  # fmt: skip
    assert code == 0

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["event"] for e in events] == ["plan", "start", "finish", "result"]
    assert events[-1]["status"] == "success"
    assert events[2]["status"] == "success"


def test_json_and_json_events_conflict(tmp_path, media):
    assert run(*pack_args(media, tmp_path, "--json", "--json-events")) == 2


def test_inspect_command(tmp_path, media, capsys):
    run(*pack_args(media, tmp_path))
    capsys.readouterr()

    assert run("inspect", str(tmp_path / "a"), "--json") == 0
    entries = json.loads(capsys.readouterr().out)
    assert entries[0]["name"] == "notes.txt" and entries[0]["complete"] is True

    assert run("inspect", str(tmp_path / "a")) == 0


def test_unpack_and_pack_update_flags(tmp_path, media, capsys):
    run(*pack_args(media, tmp_path))
    assert run(*pack_args(media, tmp_path, "--update")) == 1  # everything skipped

    assert run("unpack", str(tmp_path / "a"), "-o", str(tmp_path / "o"), "-q") == 0
    assert run("unpack", str(tmp_path / "a"), "-o", str(tmp_path / "o"), "-q",
               "--update") == 1  # already up to date  # fmt: skip


def test_delete_source_needs_an_explicit_yes(tmp_path, media):
    assert run(*pack_args(media, tmp_path, "--delete-source")) == 0
    assert (media / "notes.txt").exists()  # not a terminal, no --yes: kept

    assert run(*pack_args(media, tmp_path, "--delete-source", "--yes", "--force")) == 0
    assert not (media / "notes.txt").exists()


def test_delete_source_dry_run_deletes_nothing(tmp_path, media):
    extra = ("--delete-source", "--yes", "--dry-run")
    assert run(*pack_args(media, tmp_path, *extra)) == 0
    assert (media / "notes.txt").exists()


@needs_crypto
def test_encrypt_flow_with_password_from_environment(tmp_path, media, monkeypatch):
    monkeypatch.setenv("BATCHVAULT_PASSWORD", "pw")
    assert run(*pack_args(media, tmp_path, "--encrypt")) == 0

    out = str(tmp_path / "o")
    monkeypatch.delenv("BATCHVAULT_PASSWORD")
    assert run("unpack", str(tmp_path / "a"), "-o", out, "-q") == 1

    monkeypatch.setenv("BATCHVAULT_PASSWORD", "wrong")
    assert run("unpack", str(tmp_path / "a"), "-o", out, "-q") == 1

    password_file = tmp_path / "pw.txt"
    password_file.write_text("pw\n")
    assert run("unpack", str(tmp_path / "a"), "-o", out, "-q",
               "--password-file", str(password_file)) == 0  # fmt: skip
    assert (tmp_path / "o" / "notes.txt").exists()


def test_encrypt_without_a_password_is_a_usage_error(tmp_path, media, monkeypatch):
    monkeypatch.delenv("BATCHVAULT_PASSWORD", raising=False)
    assert run(*pack_args(media, tmp_path, "--encrypt")) == 2


@needs_crypto
def test_keygen_sign_and_verify_with_public_key(tmp_path, media):
    private, public = str(tmp_path / "k.key"), str(tmp_path / "k.pub")
    assert run("keygen", "--private", private, "--public", public, "-q") == 0
    assert run("keygen", "--private", private, "--public", public, "-q") == 2

    assert run(*pack_args(media, tmp_path)) == 0
    manifest = str(tmp_path / "a" / "manifest.json")

    assert run("verify", manifest, "--public-key", public, "-q") == 2  # unsigned
    assert run("sign", manifest, "--key", private, "-q") == 0
    assert run("verify", manifest, "--public-key", public, "-q") == 0
    assert run("unpack", manifest, "-o", str(tmp_path / "o"), "--public-key", public,
               "-q") == 0  # fmt: skip


@needs_crypto
def test_pack_sign_key(tmp_path, media):
    private, public = str(tmp_path / "k.key"), str(tmp_path / "k.pub")
    run("keygen", "--private", private, "--public", public, "-q")

    assert run(*pack_args(media, tmp_path, "--sign-key", private)) == 0
    assert run("verify", str(tmp_path / "a"), "--public-key", public, "-q") == 0


def test_notify_posts_a_summary(tmp_path, media, monkeypatch):
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append(json.loads(body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/hook"

    try:
        # Success is silent by default; --notify-on always sends it.
        assert run(*pack_args(media, tmp_path, "--notify", url)) == 0
        assert received == []

        monkeypatch.setenv("BATCHVAULT_NOTIFY_URL", url)
        assert run(*pack_args(media, tmp_path, "--force", "--notify-on", "always")) == 0
        assert received[0]["result"]["status"] == "success"

        # A failure notifies by default.
        assert run(*pack_args(media, tmp_path)) == 1
        assert received[1]["result"]["status"] == "skipped"
    finally:
        server.shutdown()
        server.server_close()


def test_notification_failure_does_not_change_the_exit_code(tmp_path, media):
    code = run(*pack_args(media, tmp_path, "--notify", "http://127.0.0.1:1/x",
                          "--notify-on", "always"))  # fmt: skip
    assert code == 0


def test_invalid_notify_url_is_a_usage_error(tmp_path, media):
    assert run(*pack_args(media, tmp_path, "--notify", "ftp://nope")) == 2
