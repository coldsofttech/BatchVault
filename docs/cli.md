# Command line guide

```
batchvault <command> [options]
```

| Command | What it does |
|---|---|
| `pack` | Compress files into split volumes and write a manifest |
| `unpack` | Restore files from a manifest, folder, volume or list |
| `verify` | Check archives are complete and intact |
| `inspect` | Show what is in your archives: sizes, ratios, engine, format, completeness |
| `list files` | List the files in a folder |
| `list archives` | List the archives in a folder |
| `keygen`, `sign` | Create a signing key pair, sign a manifest |
| `doctor` | Check this machine's setup |

Add `--help` to any command for its options.

## Sources and archives

Two words are used throughout:

- A **source** is an original file: the thing you want to protect.
- An **archive** is what BatchVault makes from one source: a set of volumes
  (`report.pdf.bv.001`, `.002`, ...) plus an entry in `manifest.json`.

`pack` turns sources into archives. `unpack` turns archives back into files.
`verify` and `inspect` look at archives. All four tables start with the same
columns, **Source** (the original file), **Archive** (its first volume) and
**Status**, so the rows read the same way whichever command you run:

```
Pack: source files -> archives
 Source      Archive             Status   Details
 report.pdf  report.pdf.bv.001   OK       3 volumes, 28.4 MiB -> 9.1 MiB (32%), verified
 notes.txt   -                   SKIPPED  archive already exists, use --force

Unpack: archives -> restored files
 Source      Archive             Status   Details
 report.pdf  report.pdf.bv.001   OK       28.4 MiB restored, checksum verified

Verify: archives
 Source      Archive             Status   Details
 report.pdf  report.pdf.bv.001   OK       volumes, checksums and integrity test OK
```

`inspect` adds Engine, Format, Level, Original, Stored, Ratio and Volumes between
**Status** and **Details**. In `unpack` the Source is the file that was restored.

**Status** is the outcome for that file: `OK`, `SKIPPED` (nothing was done, and
the details say why) or `FAILED`. **Details** is the explanation: for `OK` it
describes what was done (sizes, volumes, what was checked); for `SKIPPED` and
`FAILED` it gives the reason.

Options every command has: `--json`, `-q/--quiet`, `--log-file`, `--log-level`,
`--no-log`, `--config`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything succeeded |
| 1 | Some files failed or were skipped, or a check failed |
| 2 | Usage or configuration error (bad option, missing file, bad config) |
| 3 | Unexpected error (see the log) |
| 130 | Interrupted |

## pack

```
batchvault pack [FILE...] [--list FILE|-] -o DIR [options]
```

Give files as arguments, with `--list`, or both. Each file gets its own folder
under the output folder, and one `manifest.json` records them all. Existing
archives are skipped unless you pass `--force` or `--update`.

Your folder structure is kept: a file in a subfolder is archived under the same
subfolder (see "Folders are kept" below).

| Option | Meaning | Default |
|---|---|---|
| `-o, --output DIR` | Output folder | `archives` |
| `--list FILE` | Text or JSON list of files (`-` reads standard input) | |
| `--engine auto\|native\|7zip` | Engine to use | `auto` |
| `--format xz\|gzip\|bzip2\|store` | Built-in engine compression; use `store` for video, photos and other already-compressed files ([guide](compression.md)) | `xz` |
| `--level 0-9\|auto` | Compression level, or `auto` to pick one for each file from its type ([guide](compression.md#automatic-level)) | `auto` |
| `--volume-size SIZE` | `500k`, `10m`, `2g`, ... | `10m` |
| `-j, --jobs N` | Worker threads; `0` means none, one file at a time | `0` |
| `--include GLOB`, `--exclude GLOB` | Filter files; repeatable; `@FILE` reads patterns from a file; exclude wins | |
| `--force` | Replace existing archives | |
| `--update` | Skip unchanged files, re-archive changed ones | |
| `--verify` | Test each archive after creating it | |
| `--encrypt` | Encrypt (password from `BATCHVAULT_PASSWORD` or `--password-file`) | |
| `--sign-key FILE` | Sign the manifest with this private key | |
| `--delete-source` | Delete each original after a full restore-and-compare proves the archive | |
| `--yes` | Do not ask before `--delete-source` deletes | |
| `--retries N` | Retry a file after an I/O or engine error | `3` |
| `--manifest PATH` | Manifest location | `<output>/manifest.json` |
| `--root DIR` | Advanced: store paths relative to `DIR` instead of the automatic choice | automatic |
| `--7z PATH` | 7-Zip executable (or `BATCHVAULT_7Z`) | found automatically |
| `--allow-archives` | Allow archiving volumes and manifests | |
| `--strict` | Treat settings the engine cannot honour as errors | |
| `--dry-run` | Show what would happen, write nothing | |
| `--notify URL`, `--notify-on` | Webhook summary (`failure`, `success`, `always`) | `failure` |
| `--json-events` | One JSON event per line on standard output | |

`--delete-source` never deletes without an explicit `--yes` unless you answer
`y` at a terminal prompt.

### Folders are kept

Your folder structure is kept automatically; no option is needed. A file in a
subfolder is archived under the same subfolder, and `unpack` puts it back in the
same place.

```
D:\Media\report.pdf
D:\Media\Holidays\holiday.mp4

batchvault list files D:\Media -o files.json
batchvault pack --list files.json -o archives
archives\Media\report.pdf\report.pdf.bv.001
archives\Media\Holidays\holiday.mp4\holiday.mp4.bv.001

batchvault unpack archives -o restored
restored\Media\report.pdf
restored\Media\Holidays\holiday.mp4
```

Where the stored paths start from:

1. **Files from `list files`:** the folder you listed is kept, including its own
   name (`Media` above). `list files` records this in its output (a `root` value
   in JSON, or a `# batchvault-root:` first line in a text list).
2. **Files you give directly** (arguments, or a hand-written list): the folders
   below the deepest folder they all share are kept. Files that sit directly in
   that folder are stored without a folder, so `pack a.pdf b.pdf` from one folder
   gives `archives\a.pdf\...` and `archives\b.pdf\...`.
3. **`--root DIR`** (optional): overrides both. Paths are stored relative to `DIR`,
   and every file must be inside it. `--root D:\` keeps `Media`; `--root D:\Media`
   leaves it out.

Notes:

- The folders are recorded in `manifest.json`, so restoring needs it. Archives
  restored without their manifest (from a single volume, for example) come back
  flat.
- Files with the same name in different folders are fine. Only two files that
  would end up in the very same place are refused.
- `--update`, `verify --against` and the result tables all use the full path, so
  two `notes.txt` files in different folders are never confused.
- If you edit a list so that a file is no longer inside its recorded folder, that
  file falls back to rule 2.

## unpack

```
batchvault unpack SOURCE -o DIR [options]
```

`SOURCE` can be a manifest, a folder, any single volume (`.003` finds the whole
set), or a list of volumes. With a manifest, volumes and the restored file are
checked against their checksums, and nothing is placed in the output folder if a
check fails. Existing files are skipped unless `--force` is given; `--update`
skips files that already match and replaces the rest.

Other options: `-j`, `--retries`, `--7z`, `--password-file`, `--public-key FILE`
(require a valid manifest signature first), and `--max-output-size SIZE`.

`--max-output-size` (for example `50g`) refuses any archive that would expand
beyond that size, before anything is written. Use it when you restore archives
you do not trust: a tiny archive can be built to expand to terabytes. With a
manifest you are already protected, because an archive may never expand beyond
the size the manifest records for its file.

Files are written with owner-only permissions on Linux and macOS.

## verify

```
batchvault verify SOURCE [--against FOLDER] [--public-key FILE]
```

Checks volume sizes and checksums, then the archive's own integrity test.
`--against` also compares each archive with its original, looked up by file name
in that folder and then in its subfolders.

## inspect

```
batchvault inspect ARCHIVES [--json]
```

Shows what is in a set of **archives** you already made. It does not look at the
original files. `ARCHIVES` is where the archives are: a manifest, an archive
folder, any single volume, or a list of volumes (the same inputs as `unpack` and
`verify`; `--help` calls this argument `SOURCE`).

It looks like the other commands: a panel with the totals, then one row per
archived file, then a summary line.

```
BatchVault inspect
  Archives  archives
  Files     2
  Size      28.4 MiB -> 9.1 MiB (32%)

Inspect: archives
 Source        Archive             Status  Engine  Format  Level  Original  Stored   Ratio  Volumes  Details
 report.pdf    report.pdf.bv.001   OK      native  xz      5      28.4 MiB  9.1 MiB  32%    3
 secrets.db    secrets.db.bv.001   MISSING native  xz      5      1.0 MiB   0.4 MiB  40%    2        encrypted, some volumes are missing

Summary: 2 archives, 1 with missing volumes
```

| Column | Meaning |
|---|---|
| Source | The original file (with its stored folder, if any) |
| Archive | The first volume of its archive |
| Status | `OK` if every volume the manifest lists is present, `MISSING` if not, `-` when there is no manifest to check against |
| Engine, Format, Level | How it was archived (native or 7-Zip; xz, gzip, bzip2, store or 7z; 0-9) |
| Original, Stored | Size of the original file, and the total size of its volumes |
| Ratio | Stored size as a percentage of the original |
| Volumes | How many volumes there are |
| Details | `encrypted`, `some volumes are missing`, or `no manifest: sizes and checks unknown` |

Without a manifest, only the file name, engine (guessed from `.bv` or `.7z`),
stored size and volume count are known; the other columns show `-`.

`inspect` is a quick look. It checks that the volume files exist, but it does not
read them or check checksums; use `verify` for that. `--json` prints the same
information for scripts.

```
batchvault inspect archives
batchvault inspect archives/manifest.json --json
batchvault inspect archives/report.pdf/report.pdf.bv.002
```

## list

```
batchvault list files FOLDER [-o FILE | --output FILE] [--include GLOB] [--exclude GLOB]
batchvault list archives FOLDER [-o FILE | --output FILE]
```

With no `-o` / `--output`, a JSON list goes to standard output. With it, the
extension decides: `.json` writes JSON, anything else writes one path per line.
Both can be fed straight back into `pack --list` / `unpack`.

```
batchvault list files D:\Media -o files.json
batchvault list archives archives --output found.txt
```

If you run `list files` on an archive folder, or `list archives` on a plain
folder, a warning suggests the other command.

## keygen and sign

```
batchvault keygen --private batchvault.key --public batchvault.pub
batchvault sign archives/manifest.json --key batchvault.key
```

Needs `batchvault[crypto]`. If `BATCHVAULT_KEY_PASSWORD` is set, the private key
is stored encrypted. Keep the private key secret; share the public key.

## File names that start with a dash

Put `--` before the file names so they are never read as options:

```
batchvault pack -o archives -- -notes.txt --weird-name.csv
```

## Input lists

A text list has one path per line. Blank lines and lines starting with `#` are
ignored, and paths may contain spaces and Unicode (do not add quotes). A JSON
list is an array of paths or `{"path": ...}` objects, which is exactly what
`list files` prints.

## Examples

Archive two files with a progress bar and a final summary:

```
batchvault pack report.pdf "holiday video.mp4" -o archives
```

Archive everything in a folder except temporary files, 1 GB volumes, using the
built-in engine, and check the result:

```
batchvault list files D:\Media --exclude "*.tmp" | batchvault pack --list - \
  -o archives --engine native --volume-size 1g --verify
```

Preview without writing anything:

```
batchvault pack --list files.txt -o archives --dry-run
```

Re-run later and only archive what changed:

```
batchvault pack --list files.txt -o archives --update
```

Encrypt, then restore:

```
export BATCHVAULT_PASSWORD='correct horse'     # PowerShell: $env:BATCHVAULT_PASSWORD = '...'
batchvault pack secrets.db -o vault --encrypt
batchvault unpack vault -o restored
```

Sign the manifest and refuse to restore anything that was changed:

```
batchvault keygen
batchvault pack --list files.txt -o archives --sign-key batchvault.key
batchvault verify archives --public-key batchvault.pub
batchvault unpack archives -o restored --public-key batchvault.pub
```

Archive, then delete the originals once each archive is proven good:

```
batchvault pack --list files.txt -o archives --delete-source --yes
```

Restore a single file from one of its volumes:

```
batchvault unpack archives/report.pdf/report.pdf.bv.002 -o restored
```

Use it in a script:

```
batchvault pack --list files.txt -o archives --json > result.json
echo $?     # 0 if everything worked
```

Watch progress from another program (one JSON object per line):

```
batchvault pack --list files.txt -o archives --json-events
```

Get a message in Slack, Teams or Discord when something goes wrong:

```
export BATCHVAULT_NOTIFY_URL='https://hooks.example.com/...'
batchvault pack --list files.txt -o archives
```
