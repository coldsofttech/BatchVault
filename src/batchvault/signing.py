"""Ed25519 signatures for manifests (needs ``batchvault[crypto]``).

The signature is stored next to the manifest as ``<manifest>.sig`` and covers
the exact bytes of the manifest file. Anyone with the public key can verify it;
only the holder of the private key can create it.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

from batchvault.crypto import require_cryptography
from batchvault.errors import InputError, SignatureError

SIGNATURE_SUFFIX = ".sig"


def signature_path(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + SIGNATURE_SUFFIX)


def _write_private(path: Path, data: bytes) -> None:
    # Create the file readable by its owner only, before any key is written.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def generate_keypair(
    private_path: Path,
    public_path: Path,
    *,
    password: str | None = None,
    overwrite: bool = False,
) -> None:
    """Create an Ed25519 key pair as PEM files.

    The private key is encrypted with ``password`` when one is given.
    """
    require_cryptography()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    for path in (private_path, public_path):
        if path.exists() and not overwrite:
            raise InputError(f"{path} already exists. Use --force to replace it.")

    key = Ed25519PrivateKey.generate()
    encryption = (
        serialization.BestAvailableEncryption(password.encode("utf-8"))
        if password
        else serialization.NoEncryption()
    )
    _write_private(
        private_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            encryption,
        ),
    )
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def load_private_key(path: Path, password: str | None = None):
    require_cryptography()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        key = serialization.load_pem_private_key(
            path.read_bytes(), password.encode("utf-8") if password else None
        )
    except OSError as exc:
        raise SignatureError(f"Cannot read private key {path}: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise SignatureError(
            f"Cannot load private key {path} (wrong password or not a key): {exc}"
        ) from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise SignatureError(f"{path} is not an Ed25519 private key.")
    return key


def load_public_key(path: Path):
    require_cryptography()
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = serialization.load_pem_public_key(path.read_bytes())
    except OSError as exc:
        raise SignatureError(f"Cannot read public key {path}: {exc}") from exc
    except (ValueError, TypeError) as exc:
        raise SignatureError(f"Cannot load public key {path}: {exc}") from exc

    if not isinstance(key, Ed25519PublicKey):
        raise SignatureError(f"{path} is not an Ed25519 public key.")
    return key


def sign_manifest(manifest_path: Path, private_key) -> Path:
    """Sign the manifest with a loaded private key; return the signature file."""
    signature = private_key.sign(manifest_path.read_bytes())
    target = signature_path(manifest_path)
    target.write_text(base64.b64encode(signature).decode("ascii") + "\n")
    return target


def verify_manifest_bytes(
    data: bytes, manifest_path: Path, public_key_path: Path
) -> None:
    """Raise SignatureError unless ``data`` carries a valid signature.

    ``data`` is the manifest content, already read from ``manifest_path``; the
    signature file sits next to it. Checking the bytes that are then parsed
    leaves no gap in which the file could be swapped.
    """
    from cryptography.exceptions import InvalidSignature

    public_key = load_public_key(public_key_path)
    sig_file = signature_path(manifest_path)
    if not sig_file.is_file():
        raise SignatureError(
            f"{manifest_path.name} is not signed (no {sig_file.name})."
        )

    try:
        signature = base64.b64decode(sig_file.read_text().strip(), validate=True)
        public_key.verify(signature, data)
    except (ValueError, InvalidSignature) as exc:
        raise SignatureError(
            f"Signature check failed for {manifest_path.name}: the manifest was "
            "changed or signed with a different key."
        ) from exc


def verify_manifest_signature(manifest_path: Path, public_key_path: Path) -> None:
    """Raise SignatureError unless the manifest has a valid signature."""
    try:
        data = manifest_path.read_bytes()
    except OSError as exc:
        raise SignatureError(f"Cannot read {manifest_path}: {exc}") from exc
    verify_manifest_bytes(data, manifest_path, public_key_path)
