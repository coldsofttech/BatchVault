"""The ``keygen`` and ``sign`` commands."""

from __future__ import annotations


def cmd_keygen(args) -> int:
    import json
    import os
    from pathlib import Path

    from rich.markup import escape

    from batchvault.archive import signing
    from batchvault.cli._common import ENV_KEY_PASSWORD, _consoles

    private, public = Path(args.private).expanduser(), Path(args.public).expanduser()
    signing.generate_keypair(
        private,
        public,
        password=os.environ.get(ENV_KEY_PASSWORD) or None,
        overwrite=args.force,
    )
    if args.json:
        print(json.dumps({"private": str(private), "public": str(public)}))
    elif not args.quiet:
        out, _ = _consoles()
        out.print(f"Private key: {escape(str(private))} (keep it secret)")
        out.print(f"Public key:  {escape(str(public))} (share it)")
    return 0


def cmd_sign(args) -> int:
    import json
    import os
    from pathlib import Path

    from rich.markup import escape

    from batchvault.archive import signing
    from batchvault.archive.manifest import Manifest
    from batchvault.cli._common import ENV_KEY_PASSWORD, _consoles

    manifest_path = Path(args.manifest).expanduser()
    Manifest.load(manifest_path)  # refuse to sign something that is not a manifest
    key = signing.load_private_key(
        Path(args.key).expanduser(), os.environ.get(ENV_KEY_PASSWORD) or None
    )
    signature = signing.sign_manifest(manifest_path, key)
    if args.json:
        print(json.dumps({"signature": str(signature)}))
    elif not args.quiet:
        _consoles()[0].print(f"Signature written to {escape(str(signature))}")
    return 0
