# Compression

How BatchVault compresses, which format and level to choose, and what they cost
in time and memory.

## The short version

- **Already-compressed files** (video, audio, photos, ZIP/7z/RAR, most PDFs and
  Office files): use `--format store`. Nothing can shrink them, so compressing
  only wastes time and memory.
- **Text-like files** (logs, CSV, JSON, SQL dumps, source code, uncompressed
  images): the default (`xz`, level `auto`, which picks 6 for text) is a very good choice. Level 9 rarely adds
  more than a few percent.
- **Mixed folders:** use `--level auto`, which picks a level for each file from its
  type (see "Automatic level"), or keep the defaults.

```
batchvault pack --list files.json -o archives --format store     # videos, photos, archives
batchvault pack --list files.json -o archives                    # defaults: xz, level auto
```

## What happens to a file

1. **Native engine:** the file is written into a tar stream, cut into 16 MiB
   blocks, and each block is compressed on its own (in parallel, up to `--jobs`
   at a time). The compressed blocks are joined and split into volumes of
   `--volume-size`.
2. **7-Zip engine:** 7-Zip builds a `.7z` archive (LZMA2) and splits it into
   volumes itself.
3. If you encrypt (`--encrypt`), the data is compressed first and encrypted
   after, so encryption does not hurt the compression ratio.

Volume size has no effect on how well data compresses; it only decides where the
archive is cut.

## Formats

`--format` applies to the **native** engine. The 7-Zip engine always writes 7z
(LZMA2) and ignores it with a warning (an error with `--strict`).

| Format | What it is | Ratio | Speed | Memory | Choose it when |
|---|---|---|---|---|---|
| `xz` (default) | LZMA2 | Best | Slow | Highest at high levels | Text, data and anything that compresses well; archives you keep long term |
| `gzip` | Deflate | Good | Fast | Very low | You want speed, or a quick backup of text-like data |
| `bzip2` | Block sorting | Between gzip and xz | Slowest | Low | Rarely; mostly for compatibility |
| `store` | No compression | None | Fastest | Lowest | Video, audio, photos, other archives |

Notes:

- `store` still splits the file into volumes and adds the manifest and checksums.
  It is the right choice when you only need the splitting and the safety checks.
- With `store`, `--level` has no effect.
- Native `xz` and the 7-Zip engine both use LZMA2, so on the same data their
  sizes are close.

## Levels

`--level` goes from 0 to 9 (default `auto`; a fixed level is 5 when unsure). Higher levels search harder for
repetition: smaller files, but more time and more memory.

| Level | Meaning |
|---|---|
| 0 | No compression (gzip stored, xz fastest preset). With the 7-Zip engine, level 0 is "copy" mode, the same as store |
| 1 to 3 | Fast; a good fit for quick backups |
| 4 to 6 | Balanced. **5 is the default** |
| 7 to 9 | Slow and memory hungry; gains over 6 are usually small |

Details per format:

- **xz:** the level is the xz preset (0 to 9).
- **gzip:** the level is the deflate level (0 to 9; 0 stores).
- **bzip2:** levels 1 to 9 set the block size. bzip2 has no level 0, so 0 uses 1.
- **7-Zip:** the level is `-mx`.

### Automatic level

`--level auto` picks a level for each file from its type, so one command can
handle a mixed folder sensibly:

```
batchvault pack --list files.json -o archives --level auto
```

BatchVault looks at the file's first bytes (its signature) and then its extension.
The signature wins, so a video renamed to `.txt` is still recognised.

| Type | Level | Why |
|---|---|---|
| Already compressed: video (mp4, mkv, mov, avi, webm), photos (jpg, png, gif, webp, heic, camera raw), audio (mp3, aac, flac, ogg), archives (zip, 7z, rar, gz, xz, bz2, zst), Office and ebook files (docx, xlsx, pptx, odt, epub) | 0 | Nothing to gain; a higher level only costs time and memory |
| Text and code: txt, log, csv, json, xml, html, sql, md, source files | 6 | Highly repetitive; higher levels add little |
| Uncompressed pictures and sound: wav, bmp, tiff | 6 | Large savings |
| Programs, disk images, databases, tar: exe, dll, iso, vmdk, sqlite | 5 | Compressible, but the strongest levels cost a lot of memory for little |
| PDF | 3 | Usually partly compressed already |
| Anything else | 5 | The default |

Things to know:

- Level 0 means the lowest level of the chosen compressor. The file is still run
  through it (fast, almost no gain), and the archive format stays the same.
  If you want files that cannot shrink to skip compression entirely, use
  `--format store` instead.
- The level used for each file is shown in the result (`level 6 (text)`) and
  recorded in the manifest, so `--update` re-archives a file only if its chosen
  level changes.
- It judges the type, not the contents. A `.bin` or `.dat` file that happens to
  be already compressed gets level 5 and will not shrink; use `--format store` for
  those.
- With `--format store`, `auto` has no effect.
- `auto` never picks 7, 8 or 9, so it also keeps memory use down.

### Memory

Each compression thread needs its own memory, so total use is roughly
`threads x memory per thread`, plus the blocks waiting to be compressed
(`--jobs` is the thread budget).

Approximate xz memory per thread:

| xz level | Compress | Decompress |
|---|---|---|
| 0 | 3 MiB | 1 MiB |
| 1 | 9 MiB | 2 MiB |
| 2 | 17 MiB | 3 MiB |
| 3 | 32 MiB | 5 MiB |
| 4 | 48 MiB | 5 MiB |
| 5 and 6 | 94 MiB | 9 MiB |
| 7 | 186 MiB | 17 MiB |
| 8 | 370 MiB | 33 MiB |
| 9 | 674 MiB | 65 MiB |

At level 9 with 8 threads that is more than 5 GiB. If a run is slow or the machine
starts swapping, lower the level or `--jobs`. gzip and bzip2 need only a few MiB
per thread. Encrypting adds about 128 MiB for a moment while the password is
turned into a key.

## Which data compresses

| Data | Typical result |
|---|---|
| MP4, MKV, MOV, MP3, AAC, JPEG, PNG, WebP | 0 to 2 percent smaller; use `store` |
| ZIP, 7z, RAR, GZ, XZ, DOCX, XLSX, PPTX | About 0 percent; use `store` |
| PDF | Varies, often little |
| Text, logs, CSV, JSON, XML, SQL dumps, source code | 70 to 90 percent smaller |
| WAV, BMP, TIFF, raw images | Often 30 to 70 percent smaller |
| Databases, VM and disk images | Depends on how empty they are; often large savings |
| Encrypted files | About 0 percent: encrypted data looks random |

You can tell whether compression helped from the result table after `pack`:

```
report.pdf   OK   report.pdf.bv.001   3 volumes, 28.4 MiB -> 9.1 MiB (32%)
holiday.mp4  OK   holiday.mp4.bv.001  3 volumes, 28.4 MiB -> 28.2 MiB (99%)
```

A percentage near 100 means the file was already compressed. Next time use
`--format store` for files like that.

## Block compression and ratio

The native engine compresses 16 MiB blocks independently. This is what lets it use
all your threads on a single large file, and a block can be read without the
others. The cost is that repetition between distant blocks is not exploited, so
for data with a lot of long-range repetition the result can be slightly larger
than one big xz stream. For most data the difference is small. Files smaller than
one block are not affected.

## Getting your files back without BatchVault

- **Native, not encrypted:** joining the volumes gives a standard file. For
  example `cat name.bv.* > name.tar.xz` (gzip: `.tar.gz`, bzip2: `.tar.bz2`,
  store: `.tar`), and any tar tool opens it.
- **Native, encrypted:** only BatchVault can restore it.
- **7-Zip:** open the `.001` volume with 7-Zip.

## Recipes

```
# Course videos and photos: just split, keep checksums
batchvault pack --list files.json -o archives --format store --volume-size 500m

# Logs and database dumps: strong compression, modest memory
batchvault pack --list files.json -o archives --level 6 --jobs 4

# A mixed folder: each file gets the level that suits its type
batchvault pack --list files.json -o archives --level auto

# Fast first pass over a big mixed folder
batchvault pack --list files.json -o archives --format gzip --level 3

# Smallest possible size, plenty of memory and time
batchvault pack --list files.json -o archives --level 9 --jobs 2

# 7-Zip engine, copy mode (same as store)
batchvault pack --list files.json -o archives --engine 7zip --level 0
```

`--level auto` chooses the level for each file but does not change the format.
The ratio shown after each file tells you when a file would have been better
stored with `--format store`.
