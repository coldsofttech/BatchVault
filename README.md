# BatchVault

[![Tests](https://github.com/coldsofttech/BatchVault/actions/workflows/tests.yml/badge.svg)](https://github.com/coldsofttech/BatchVault/actions/workflows/tests.yml)
[![E2E Tests](https://github.com/coldsofttech/BatchVault/actions/workflows/e2e.yml/badge.svg)](https://github.com/coldsofttech/BatchVault/actions/workflows/e2e.yml)
[![Lint](https://github.com/coldsofttech/BatchVault/actions/workflows/lint.yml/badge.svg)](https://github.com/coldsofttech/BatchVault/actions/workflows/lint.yml)
[![Release](https://img.shields.io/github/v/release/coldsofttech/BatchVault?logo=github)](https://github.com/coldsofttech/BatchVault/releases)
[![PyPI](https://img.shields.io/pypi/v/batchvault?logo=pypi&logoColor=white)](https://pypi.org/project/batchvault/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License](https://img.shields.io/github/license/coldsofttech/BatchVault)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)

Archive, compress, split and restore many files in one go, from the command
line, from Python, from a GitHub workflow, or from a container.

## Why BatchVault?

Backing up or moving a pile of large files usually means a shell loop around
`7z`, with no record of what went where. When a copy goes missing or gets
damaged, you only find out when you try to restore.

BatchVault does the whole job and keeps proof:

- Every file becomes its own set of fixed-size volumes, so huge files fit
  through upload limits, small disks and flaky links.
- A **manifest** records every file and volume with its checksum, so
  restoring never depends on guessing folder names.
- Restoring checks each volume and the finished file against those checksums
  before anything lands in your output folder.
- It works the same on Windows, Linux and macOS, with or without 7-Zip.

## What you get

- Compress a list of files into volumes of a size you choose (`10m`, `2g`, ...).
- Restore from a manifest, a folder, any single volume, or a list of volumes.
- Verify archives are complete and intact, and optionally compare them with the
  originals.
- Pick up where you left off: `--update` skips what has not changed.
- Optional **encryption** (AES-256) and **signed manifests** to prove nothing
  was tampered with.
- `--delete-source` removes an original only after the archive has been
  restored and matched byte for byte.
- A live progress bar with an ETA, readable summaries, and JSON output for
  scripts.
- Include and exclude patterns, retries, webhook notifications and a
  settings file.
- A Python API, a GitHub Action and Docker images.

## Install

```
pip install batchvault
```

Requires Python 3.11.4 or later. Works with [pipx](https://pipx.pypa.io/) and
[uv](https://docs.astral.sh/uv/) too:

```
pipx install batchvault
uv tool install batchvault
```

For encryption and signed manifests, add the extra:

```
pip install "batchvault[crypto]"
```

7-Zip is optional. If you want to use it, install it from
[7-zip.org](https://www.7-zip.org/) (`7zz` on Linux and macOS, for example
`brew install sevenzip`, or `apt install 7zip`).

## Engines

BatchVault has two engines. `--engine auto` (the default) uses 7-Zip when it
finds it and the built-in engine otherwise.

| | Built-in (`native`) | 7-Zip (`7zip`) |
|---|---|---|
| Needs anything installed | No | An installed 7-Zip |
| Volume names | `name.bv.001`, `.002`, ... | `name.7z.001`, `.002`, ... |
| Compression | xz (default), gzip, bzip2 or none | 7z (LZMA2) |
| Levels | 0-9 (bzip2 has no 0; 1 is used) | 0-9 |
| Volume size, parallel jobs | Yes | Yes |
| Encryption | Yes, needs `batchvault[crypto]` | Yes |
| Plain-tool recovery | Unencrypted volumes join into a normal `.tar.xz` / `.tar.gz` / `.tar.bz2` (`cat name.bv.* > name.tar.xz`) | Opens in any 7-Zip |

Things to know:

- `--format` only applies to the built-in engine. 7-Zip always writes 7z, and
  BatchVault warns if you ask for something else (`--strict` makes that an
  error).
- 7-Zip can only take a password on its command line, so other users on a
  shared machine could see it while it runs. Prefer the built-in engine there.
- An encrypted built-in archive cannot be opened with standard tools; use
  BatchVault to restore it.
- Several engines can live in one archive folder: the manifest records which
  one made each file.
- Videos, photos and other already-compressed files do not shrink, whatever the
  level. For them use `--format store`. See the
  [compression guide](docs/compression.md) for choosing a format and level, and
  for memory use.

## Use

| I want to use BatchVault... | Read |
|---|---|
| in a terminal | [Command line guide](docs/cli.md) |
| in my own Python code | [Python guide](docs/python.md) |
| in a GitHub workflow | [GitHub Action guide](docs/github-action.md) |
| in a container | [Docker guide](docs/docker.md) |
| choosing a format and level | [Compression guide](docs/compression.md) |

A **source** is an original file; an **archive** is the set of volumes made from
it. `pack` turns sources into archives, `unpack` turns archives back into files.

A first run:

```
# 1. List the files you want to archive
batchvault list files D:\Media --exclude "*.tmp" -o files.txt

# 2. Archive them into 100 MB volumes, checking each archive afterwards
batchvault pack --list files.txt -o archives --volume-size 100m --verify

# 3. Look at what is in your archives (sizes, ratios, volumes, ...)
batchvault inspect archives

# 4. Restore everything somewhere else
batchvault unpack archives -o restored
```

Your folder structure is kept automatically. Each file gets its own folder, inside
a copy of the folders it came from, plus a manifest:

```
batchvault list files D:\Media -o files.json
batchvault pack --list files.json -o archives

archives/
  manifest.json
  Media/
    Holidays/
      holiday.mp4/
        holiday.mp4.bv.001
        holiday.mp4.bv.002
    report.pdf/
      report.pdf.bv.001
```

`unpack` puts every file back in the same place (`restored\Media\Holidays\holiday.mp4`),
and files with the same name in different folders do not clash.

The folder you listed (`Media`) is kept because `list files` remembers it. If you
give `pack` files directly instead, the folders below the one they all share are
kept; files that sit directly in it are not nested.

## Cryptography (optional)

Encryption and signed manifests need one extra package. Everything else works
without it.

```
pip install "batchvault[crypto]"
```

The Docker images already include it. Without the extra, asking for encryption
or signing stops with a message telling you to install it.

### Encrypt your archives

```
export BATCHVAULT_PASSWORD='a long passphrase'     # PowerShell: $env:BATCHVAULT_PASSWORD = '...'
batchvault pack secrets.db -o vault --encrypt
batchvault unpack vault -o restored
```

- Encrypting and decrypting use about 128 MiB of memory for a moment while the
  password is turned into a key; that is deliberate, to make guessing slow.
- The password comes from `BATCHVAULT_PASSWORD` or `--password-file`, never from
  a command-line option, so it stays out of your shell history.
- Restoring and verifying need the same password. A wrong password or a damaged
  archive is reported as an error; nothing is written to your output folder.
- Tampering is detected too: changed, reordered or missing pieces of an encrypted
  archive fail the check instead of restoring bad data.
- **There is no way to recover encrypted data if the password is lost.**

What is and is not hidden:

- The contents of each file are encrypted.
- File names, sizes and checksums still appear in `manifest.json` and in the
  output folder names. Keep the manifest private, or do not put sensitive
  names in file names.
- The 7-Zip engine encrypts with 7-Zip's own AES-256 (and hides the names inside
  the archive), but 7-Zip can only take the password on its command line, so
  other users on the same machine could see it while the command runs. On a
  shared machine, use the built-in engine.

### Sign your manifest

A signature proves the manifest was made by you and has not been changed since,
so restoring from an untrusted copy can be refused.

```
batchvault keygen                                   # creates batchvault.key and batchvault.pub
batchvault pack --list files.txt -o archives --sign-key batchvault.key
batchvault verify archives --public-key batchvault.pub
batchvault unpack archives -o restored --public-key batchvault.pub
```

- Keep `batchvault.key` secret (set `BATCHVAULT_KEY_PASSWORD` before `keygen` to
  store it encrypted). Share only `batchvault.pub`.
- The signature is saved next to the manifest as `manifest.json.sig`. Adding more
  files to the archive folder later changes the manifest, so sign it again; an
  old signature is removed automatically and you get a warning.
- With `--public-key`, a missing, wrong or outdated signature stops the run
  before anything is restored.

## Config file

Keep your usual options in a `batchvault.toml` next to your files, or in
`[tool.batchvault]` inside `pyproject.toml`:

```toml
[pack]
level = 7
volume-size = "100m"
exclude = ["*.tmp"]
update = true
```

Command-line options always win over the file. See
[docs/configuration.md](docs/configuration.md) for every setting and the
environment variables.

## Check your setup

```
batchvault doctor
```

shows whether Python has the compression modules it needs, whether 7-Zip was
found, and whether the log folder is writable. If something is missing it says
what to install.

## Logs

Every run writes a log file you can attach to a bug report. Passwords are never
logged, and web addresses with credentials in them are masked.

| OS | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\BatchVault\Logs` |
| macOS | `~/Library/Logs/BatchVault` |
| Linux | `$XDG_STATE_HOME/batchvault` (default `~/.local/state/batchvault`) |

The newest 20 logs are kept. Change the folder with `BATCHVAULT_LOG_DIR`, pick a
file with `--log-file`, or turn logging off with `--no-log`.

## Contributing and security

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and
follow the [code of conduct](CODE_OF_CONDUCT.md). To report a vulnerability,
please read [SECURITY.md](SECURITY.md) first and do not open a public issue.
Technical notes live in [docs/architecture.md](docs/architecture.md) and
[docs/development.md](docs/development.md).

## License

See [LICENSE](LICENSE). BatchVault depends on [Rich](https://github.com/Textualize/rich)
(MIT) and [pathspec](https://github.com/cpburnett/python-pathspec) (MPL-2.0), and
optionally on [cryptography](https://github.com/pyca/cryptography). 7-Zip is a
separate program with its own license and is never bundled.
