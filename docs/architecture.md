# Architecture

This document records the design as implemented. See `.temp/REQUIREMENTS.md`
for the original requirements.

## Layers

The command-line interface is a thin wrapper over the Python API in `api.py`.
The API never imports `rich`; it reports progress through an optional callback
and the CLI renders it.

```
src/batchvault/
  api.py            pack, unpack, verify, inspect, list_files, list_archives
  cli.py            argparse commands, exit codes, JSON / event output
  ui.py             Rich progress, tables, panels (CLI only)
  notify.py         webhook notifications (CLI only)
  config.py         batchvault.toml / [tool.batchvault] -> option defaults
  models.py         Status, FileResult, RunResult, ProgressEvent, InspectEntry
  options.py        PackOptions, size parsing, formats
  errors.py         exception hierarchy
  inputs.py         list files (text/JSON/stdin), dedupe, conflict checks
  filters.py        include / exclude via pathspec (gitignore-style)
  locate.py         resolve a manifest / folder / volume / list to archives
  manifest.py       manifest read / write / validate, SHA-256 helpers
  volumes.py        volume naming, discovery, writer/reader, staging cleanup
  blocks.py         block-parallel compression, format detection
  crypto.py         optional AES-256-GCM record encryption (native engine)
  signing.py        optional Ed25519 manifest signatures
  logs.py, security.py, eta.py, doctor.py
  engines/          base, native, sevenzip, registry
```

## Engines

| Engine | Format | Volumes |
|---|---|---|
| native | `tar` stream, compressed in independent blocks (xz, gzip, bzip2 or store), byte-split | `name.bv.001`, `.002`, ... |
| 7zip | `.7z` (LZMA2) through an installed 7-Zip executable | `name.7z.001`, ... |

- Native uses the standard library. Concatenated xz, gzip and bzip2 members are
  valid single streams, so an unencrypted joined volume set (`cat x.bv.*`) is a
  normal `.tar.xz`, `.tar.gz` or `.tar.bz2`.
- 7-Zip is run with `subprocess` and never bundled.
- Archives are built in a `.batchvault-*` staging folder and moved into place
  only when complete, so a failed or interrupted run never damages an existing
  archive. Staging folders older than a day are removed on the next run.
- `--jobs` is a total thread budget, split into files in parallel and threads
  per file (`-mmt` for 7-Zip, block threads for native).

## Manifest

JSON, `schema_version` 1: one entry per source file with name, `folder` (its
folder under the stored root, forward slashes, `""` when not in a subfolder;
chosen by `inputs.assign_folders`: an explicit `--root`, else the root recorded by
`list files`, else the deepest folder the files share; validated so
it can never be absolute or contain `..`), size, SHA-256,
engine, format, level, volume size, `encrypted`, `mtime_ns` and an ordered
volume list (relative path, size, SHA-256). Restoration never depends on folder
naming. Volume paths are relative to the manifest folder. The manifest is
written atomically and merged across runs.

Because the manifest records every volume, a missing last volume is detected;
without a manifest only gaps in the numbering can be.

## Encryption (`batchvault[crypto]`)

- **Native:** a header (magic, format, random salt and nonce prefix) followed by
  one AES-256-GCM record per compressed block and an authenticated end marker.
  The key comes from the password with scrypt (N = 2^17, about 128 MiB of memory,
  r = 8, p = 1). The cost is stored in the header as log2(N), so the default can
  rise later, and values outside 2^14 to 2^18 are refused so a damaged header
  cannot demand huge amounts of memory. The header, the record number and a
  last-record flag are authenticated, so reordering, dropping or truncating
  records is detected. A record larger than 128 MiB is refused as damage. Encrypted volumes are not plain tar streams.
- **7-Zip:** AES-256 with header encryption (`-p`, `-mhe=on`). 7-Zip only takes
  the password on its command line, where other local users could see it while
  the process runs. Prefer the native engine on shared machines.
- Passwords come from `BATCHVAULT_PASSWORD` or `--password-file`, never a flag.

## Signing (`batchvault[crypto]`)

Ed25519. `<manifest>.sig` holds a base64 signature over the exact manifest
bytes. `unpack` and `verify` accept `--public-key` to require a valid signature
before touching any archive. The signature is checked on the exact bytes that are
then parsed (`locate.resolve_targets` takes a manifest loader), so the file
cannot be swapped between the check and the use. A signature proves who made a
manifest, not that it is the latest one: replacing a manifest and its volumes
with an older, validly signed set is not detected. Changing the manifest without re-signing removes
the stale signature and warns.

## Safe source cleanup

`pack --delete-source` restores each archive to a temporary folder, compares its
SHA-256 with the original, checks the original did not change, and only then
deletes it. A terminal confirmation is required unless `--yes` is given;
without a terminal and without `--yes` nothing is deleted.

## Incremental mode, retries and resume

- `pack --update` skips a file when its size, modification time and the
  settings (engine, format, level, volume size, encryption) match the manifest,
  and re-archives it otherwise. `unpack --update` skips files that already match
  the recorded checksum.
- `--retries N` retries I/O and engine errors with exponential backoff.
  Missing files, existing outputs and corrupt data are never retried.
- Resuming is re-running with `--update`: finished files are skipped and an
  interrupted file is rebuilt, because nothing is installed until it is complete.

## Configuration

`batchvault.toml` (or `[tool.batchvault]` in `pyproject.toml`), found by walking
up from the current folder or given with `--config` / `BATCHVAULT_CONFIG`.
Precedence: command line, then config file, then built-in defaults. A file found
by searching (not named with `--config` / `BATCHVAULT_CONFIG`) is rejected if it
sets any option in `config.RESTRICTED_OPTIONS` (programs, webhooks, trusted keys,
password files, log files, deletion). See `docs/configuration.md`.

## Hardening notes

- 7-Zip is located without ever searching the current folder
  (`engines/sevenzip.py::_search_path`).
- Resource limits on restore: the native engine charges each member's size
  against a budget before extracting (budget = `--max-output-size`, and with a
  manifest also the recorded file size), and refuses more than 4 MiB of data
  after the tar end marker. The 7-Zip engine watches the output folder while
  it extracts and stops the program as soon as the folder grows past the limit,
  and refuses links and special files.
- 7-Zip is called with `-spd` so file names are never wildcard patterns.
- Volumes are made owner-only (`0600`) before they are moved into place, and the
  manifest is written through an unpredictable `mkstemp` file, also owner-only.
- `--notify` values are masked in the logged command line.
- Staging cleanup never follows symbolic links.
- CI: third-party actions are referenced by major version tag (Dependabot keeps
  them current), releases carry build provenance attestations, and pushed images
  carry provenance and an SBOM.
- The GitHub Action builds `batchvault <command> <options> -- <paths>`.
- Restored tar members must be regular files with a single-part name, must not
  be Windows-special names on Windows, must resolve inside the staging folder,
  and are extracted with Python's `data` filter (hence Python 3.11.4+).

## Output and exit codes

`--json` prints the final result; `--json-events` prints one JSON object per
line (`plan`, `start`, `finish`, `result`). Progress and warnings go to stderr.
Exit codes: 0 success, 1 failed / skipped items, 2 usage or configuration
error, 3 unexpected error, 130 interrupted.

## Logging

One timestamped log file per run in the OS default folder
(`%LOCALAPPDATA%\BatchVault\Logs`, `~/Library/Logs/BatchVault`,
`$XDG_STATE_HOME/batchvault`; override with `BATCHVAULT_LOG_DIR`), with
credential redaction, owner-only permissions and the newest 20 kept.

## Distribution

PyPI (also usable through pipx), a GitHub Action (`action.yml`), and Docker
images on GHCR (native, and `-7zip`).

## Third-party

`rich` (MIT), `pathspec` (MPL-2.0), optional `cryptography` (Apache-2.0/BSD).
7-Zip is an optional external tool.
