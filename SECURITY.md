# Security policy

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's private vulnerability reporting:
the repository's **Security** tab, then **Report a vulnerability**.

Include the version, what you did, what you expected, and what happened. You
will get a reply as soon as the maintainer can; fixes are released as patch
versions and noted in the changelog.

## Supported versions

Only the latest released version receives security fixes.

## Things worth knowing

- Settings files found automatically (in the current or a parent folder) cannot
  change which program runs, where notifications go, which keys are trusted or
  whether originals are deleted. Name the file with `--config` to trust it.
- BatchVault never runs a program from the current folder. 7-Zip is taken from
  `--7z`, `BATCHVAULT_7Z`, the `PATH` folders (absolute entries only) or its
  usual install folders.
- Archives, volumes and `manifest.json` are created readable only by their owner
  on Linux and macOS (on Windows, folder permissions apply).
- When restoring archives you do not trust, use `--max-output-size`; with a
  manifest, an archive can never expand beyond the size it records. 7-Zip links
  and special files are refused, and 7-Zip file names are never treated as
  wildcards.
- Webhook URLs (which carry a secret) are masked in the log file; the notification
  itself contains file names and paths, so use `https://`.
- Restoring only writes plain files with simple names. Links, devices, folders
  inside the archive and names Windows treats specially (`C:x`, `NUL`, ...) are
  refused.

- Archives made with `--encrypt` are protected by your password; there is no
  way to recover data if it is lost.
- The 7-Zip engine passes the password on 7-Zip's command line, where other
  users of the same machine could see it while the process runs. Use the native
  engine on shared machines.
- A signature proves who made a manifest, not that it is the newest one. Someone
  who can replace both the manifest and the volumes with an older, validly
  signed set will not be detected; keep your own record of what the latest
  version should be if that matters to you.
- On Windows, owner-only file permissions are not applied by BatchVault; private
  keys, logs and archives rely on the permissions of the folder they are in.
  Keep them in a folder only you can read.
- Encrypting or decrypting briefly needs about 128 MiB of memory for the password
  hashing, on top of the compression work.
- Signing and encryption need `pip install "batchvault[crypto]"`.
- Keep private signing keys out of repositories and CI logs.
