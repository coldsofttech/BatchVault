import pytest


@pytest.fixture(autouse=True)
def isolated_log_dir(tmp_path_factory, monkeypatch):
    """Keep test runs out of the user's real log folder."""
    monkeypatch.setenv("BATCHVAULT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))


@pytest.fixture(autouse=True)
def fast_key_derivation(monkeypatch):
    """Use a cheap password hash so encryption tests stay quick.

    The cost is stored in each archive's header, so archives still open; one test
    checks that the real default is strong.
    """
    from batchvault import crypto

    monkeypatch.setattr(crypto, "DEFAULT_LOG2_N", 14)
