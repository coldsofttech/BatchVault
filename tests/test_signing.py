import pytest

pytest.importorskip("cryptography")

import batchvault as bv  # noqa: E402
from batchvault import signing  # noqa: E402
from batchvault.errors import InputError, SignatureError  # noqa: E402


@pytest.fixture
def keys(tmp_path):
    private, public = tmp_path / "k.key", tmp_path / "k.pub"
    signing.generate_keypair(private, public)
    return private, public


@pytest.fixture
def archived(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("hello " * 200)
    bv.pack([source], tmp_path / "a", engine="native")
    return tmp_path / "a"


def sign(archived, private):
    key = signing.load_private_key(private)
    return signing.sign_manifest(archived / "manifest.json", key)


def test_sign_and_verify(archived, keys):
    private, public = keys
    sign(archived, private)
    signing.verify_manifest_signature(archived / "manifest.json", public)


def test_tampered_manifest_fails(archived, keys):
    private, public = keys
    sign(archived, private)
    manifest = archived / "manifest.json"
    manifest.write_text(manifest.read_text().replace("notes.txt", "other.txt", 1))

    with pytest.raises(SignatureError, match="Signature check failed"):
        signing.verify_manifest_signature(manifest, public)


def test_wrong_key_fails(tmp_path, archived, keys):
    sign(archived, keys[0])
    other_private, other_public = tmp_path / "o.key", tmp_path / "o.pub"
    signing.generate_keypair(other_private, other_public)

    with pytest.raises(SignatureError):
        signing.verify_manifest_signature(archived / "manifest.json", other_public)


def test_unsigned_manifest(archived, keys):
    with pytest.raises(SignatureError, match="not signed"):
        signing.verify_manifest_signature(archived / "manifest.json", keys[1])


def test_encrypted_private_key(tmp_path):
    private, public = tmp_path / "e.key", tmp_path / "e.pub"
    signing.generate_keypair(private, public, password="pw")

    with pytest.raises(SignatureError):
        signing.load_private_key(private)
    assert signing.load_private_key(private, "pw")


def test_keygen_refuses_to_overwrite(keys):
    private, public = keys
    with pytest.raises(InputError, match="already exists"):
        signing.generate_keypair(private, public)
    signing.generate_keypair(private, public, overwrite=True)


def test_pack_signs_and_unpack_requires_signature(tmp_path, keys):
    private, public = keys
    source = tmp_path / "notes.txt"
    source.write_text("hello " * 200)
    archives = tmp_path / "a"

    bv.pack([source], archives, engine="native", sign_key=private)
    result = bv.unpack(archives, tmp_path / "out", public_key=public)
    assert result.status == bv.Status.SUCCESS

    (archives / "manifest.json").write_text(
        (archives / "manifest.json").read_text().replace("notes", "nodes", 1)
    )
    with pytest.raises(SignatureError):
        bv.unpack(archives, tmp_path / "out2", public_key=public)
    with pytest.raises(SignatureError):
        bv.verify(archives, public_key=public)


def test_repacking_without_a_key_removes_the_stale_signature(tmp_path, keys):
    source = tmp_path / "notes.txt"
    source.write_text("hello " * 200)
    other = tmp_path / "other.txt"
    other.write_text("more " * 200)
    archives = tmp_path / "a"

    bv.pack([source], archives, engine="native", sign_key=keys[0])
    result = bv.pack([other], archives, engine="native")

    assert not (archives / "manifest.json.sig").exists()
    assert any("signature was removed" in w for w in result.warnings)


def test_signature_is_checked_on_the_bytes_that_are_parsed(archived, keys):
    """The check uses the bytes it is given, not whatever is on disk right now."""
    private, public = keys
    sign(archived, private)
    manifest = archived / "manifest.json"
    genuine = manifest.read_bytes()

    signing.verify_manifest_bytes(genuine, manifest, public)  # valid

    forged = genuine.replace(b"notes.txt", b"other.txt", 1)
    with pytest.raises(SignatureError):
        signing.verify_manifest_bytes(forged, manifest, public)  # the file is fine


def test_swapping_the_manifest_after_verification_cannot_change_what_runs(
    archived, keys, monkeypatch
):
    private, public = keys
    sign(archived, private)
    manifest = archived / "manifest.json"
    genuine = manifest.read_bytes()
    forged = genuine.replace(b"notes.txt", b"other.txt")

    real_read_bytes = type(manifest).read_bytes
    reads = []

    def read_then_swap(self):
        data = real_read_bytes(self)
        if self == manifest and not reads:
            reads.append(1)
            manifest.write_bytes(forged)  # an attacker swaps it right after the read
        return data

    monkeypatch.setattr(type(manifest), "read_bytes", read_then_swap)

    result = bv.verify(archived, public_key=public)

    # What was parsed is exactly what was verified: the genuine manifest.
    assert result.status == bv.Status.SUCCESS
    assert [entry.source.name for entry in result.entries] == ["notes.txt.bv.001"]
