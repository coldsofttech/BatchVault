# Changelog

All notable changes to this project are documented here.

## [Unreleased]

- `pack --resume` and `unpack --resume` continue an interrupted run (Ctrl+C, crash, power loss, CI time limit). Both keep a journal in the output folder; `--resume` takes every setting from it, so you give only `-o` (plus the password or signing key again, which are never stored). Finished files are reused only if their source is unchanged and every volume (or restored file) still matches its checksum; anything else is redone. `unpack --resume` never overwrites or deletes a file it did not write. Without a journal `--resume` is an error, and it cannot be combined with other settings, `--force`, `--update` or `--dry-run`. Also available as `resume=True` in Python, the `resume` input of the GitHub Action and in Docker. Interrupting a run now stops starting new files instead of working through the queue.
- `pack --resume` now also continues **inside** a file that was interrupted part-way (built-in engine). The journal records every finished volume and a checkpoint after each compressed block; a resume carries on from the newest checkpoint whose data still matches its checksums, without compressing the finished blocks again, and a retry after an I/O error does the same. Damaged, missing or truncated staged data is never trusted (an older checkpoint is used, or the file starts over), and with encryption the file continues with its own header, so a different password is refused. Archives are unchanged: a new tar writer produces the same bytes as before (tested byte for byte). The staging folder is now kept in the output folder after a failure until the file is resumed or 24 hours pass. With 7-Zip, resume stays file by file.

- `pack` now writes `BATCHVAULT-README.md` next to the manifest: how the archive was made, how to install the same BatchVault version, and the exact `unpack` and `verify` commands for this archive (with the password-file or public-key option when it is encrypted or signed). It never contains a password or key, it is not treated as a volume, and a file of your own with that name is never overwritten. Turn it off with `--no-readme` (`readme=False` in Python).

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
