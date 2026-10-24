import os

from batchvault.logs import default_log_dir, prune_logs
from batchvault.security import redact


def test_redact_hides_url_credentials():
    assert redact("push https://user:secret@host/x") == "push https://***@host/x"
    assert redact("no secrets here") == "no secrets here"


def test_log_dir_can_be_overridden(tmp_path, monkeypatch):
    monkeypatch.setenv("BATCHVAULT_LOG_DIR", str(tmp_path))
    assert default_log_dir() == tmp_path


def test_prune_keeps_newest(tmp_path):
    for number in range(5):
        (tmp_path / f"batchvault-2026010{number}-1.log").write_text("x")
    (tmp_path / "other.log").write_text("x")

    prune_logs(tmp_path, keep=2)

    remaining = sorted(p.name for p in tmp_path.iterdir())
    assert remaining == [
        "batchvault-20260103-1.log",
        "batchvault-20260104-1.log",
        "other.log",
    ]
    assert os.path.exists(tmp_path / "other.log")
