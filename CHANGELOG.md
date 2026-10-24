# Changelog

All notable changes to this project are documented here.

## [Unreleased]

## [0.1.0] - 2026-10-24

First release.

- Archive many files at once into volumes of a size you choose, and restore them from a manifest, a folder, any single volume or a list.
- Two engines: a built-in one that needs nothing installed (xz, gzip, bzip2 or no compression) and an optional 7-Zip engine.
- Your folder structure is kept: archives mirror the tree and `unpack` recreates it. `list files` remembers the folder you listed, and `--root DIR` overrides where paths start.
- Every archive has a checksummed manifest; restoring checks each volume and the restored file before anything is placed in the output folder.
- `verify` and `inspect` check and describe archives, with one consistent Source / Archive / Status / Details table and live progress with an ETA.
- `--level auto` picks a compression level for each file from its type: none for video, photos and archives, stronger for text.
- `--update` skips unchanged files, `--retries` retries failures, `--dry-run` previews a run, and `--include` / `--exclude` filter files.
- Optional encryption and signed manifests (`pip install "batchvault[crypto]"`), and `--delete-source` to remove originals only after the archive is proven good.
- Safer by default: archives are readable only by their owner on Linux and macOS; `unpack --max-output-size` refuses archives that expand beyond a size you choose; settings files found automatically cannot run programs, send data or delete files; and 7-Zip is never run from the current folder.
- `batchvault.toml` settings file, `--notify` webhooks, `--json` and `--json-events` output, and per-run log files in your system's log folder.
- A Python API, a GitHub Action (`coldsofttech/BatchVault@v1`) and Docker images with and without 7-Zip.
- Sensible defaults: `--level auto`, no worker threads (`--jobs 0`) and three retries.
- Requires Python 3.11.4 or later.

[Unreleased]: https://github.com/coldsofttech/BatchVault/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/coldsofttech/BatchVault/releases/tag/v0.1.0
