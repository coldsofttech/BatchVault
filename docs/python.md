# Python guide

Everything the command line does is available as plain functions. The library
never prints and never imports the terminal UI; it returns structured results
and raises meaningful exceptions.

```python
import batchvault as bv

result = bv.pack(
    ["report.pdf", "holiday.mp4"],
    "archives",
    level=7,
    volume_size="100m",
    verify=True,
)

print(result.status)            # Status.SUCCESS / FAILED / SKIPPED / PARTIAL
for entry in result.entries:
    print(entry.source.name, entry.status, entry.message)
```

## Functions

| Function | Purpose |
|---|---|
| `bv.pack(files, output, **options)` | Archive files; returns a `RunResult` |
| `bv.unpack(source, output, **options)` | Restore files; returns a `RunResult` |
| `bv.verify(source, **options)` | Check archives; returns a `RunResult` |
| `bv.inspect(source)` | Describe existing archives (`source` is a manifest, archive folder, volume or list of volumes, not the original files); returns `list[InspectEntry]` |
| `bv.list_files(folder, include=, exclude=)` | Files in a folder; `list[FileEntry]` |
| `bv.list_archives(folder)` | Archives in a folder; `list[FileEntry]` |

Paths can be `str` or `pathlib.Path`. Sizes accept `"10m"` or a number of bytes.

### pack

```python
bv.pack(
    files=(), output="archives", *,
    list_file=None, engine="auto", seven_zip=None, format=None,
    level="auto",    # 0-9, or "auto" to pick one for each file from its type
    volume_size="10m", jobs=0,
    root=None,        # optional override for where stored folders start
    include=(), exclude=(), force=False, update=False, verify=False,
    manifest=None, allow_archives=False, strict=False, dry_run=False,
    password=None, retries=3, sign_key=None, sign_key_password=None,
    delete_source=False, confirm=None, progress=None,
)
```

- `engine`: `"auto"`, `"native"` or `"7zip"`. `format`: `"xz"`, `"gzip"`,
  `"bzip2"` or `"store"` (built-in engine only).
- `password` encrypts the archives (the built-in engine needs
  `batchvault[crypto]`). `sign_key` is the path of an Ed25519 private key.
- `delete_source=True` deletes each original after a restore-and-compare proves
  the archive. `confirm(paths) -> bool`, if given, is asked first; with no
  `confirm`, deletion is automatic once the check passes.
- `jobs` is the thread budget; `0` (the default) uses no worker threads and handles one file at a time.
- Folders are kept automatically: a file is stored under
  `<output>/<folder>/<name>/` and `unpack` recreates the tree. The folder starts
  from the root that `bv.list_files()` recorded on each `FileEntry` (so the listed
  folder's name is kept), or, for files you pass directly, from the deepest
  folder they all share. `root` overrides this with a folder of your choice.
  Files with the same name in different folders are fine. `FileResult.folder` and
  `InspectEntry.folder` hold the stored folder (`""` when the file is not in a
  subfolder).

  ```python
  files = bv.list_files("D:/Media")            # each entry remembers D:/ as its root
  bv.pack([f.path for f in files], "archives") # shared folder: keeps below D:/Media
  # to keep "Media" too, pass the listing's root:
  bv.pack([f.path for f in files], "archives", root=files[0].root)
  ```

### unpack and verify

```python
result = bv.unpack("archives", "restored", force=False, update=False,
                   password=None, public_key=None, retries=3)

result = bv.verify("archives", against="originals", password=None,
                   public_key=None)
```

`source` can be a manifest, a folder, any volume, or a list of volumes.
`public_key` requires a valid manifest signature first. `max_output_size`
(`"50g"` or bytes) refuses an archive that would expand beyond that size before
anything is written; with a manifest, an archive can never expand beyond the
size recorded for its file.

## Results

`RunResult` has:

- `status`: `Status.SUCCESS`, `FAILED`, `SKIPPED` or `PARTIAL`
- `entries`: one `FileResult` per file (`source`, `status`, `message`, `size`,
  `volumes`, `warnings`, `archive`, `stored_size`, `restored`, `name`, `folder`). For `pack`,
  `source` is the original file and `archive` its first volume; for `unpack` and
  `verify`, `source` is the archive (its first volume) and `restored` lists the
  files written. `message` describes what happened, for example
  `3 volumes, 28.4 MiB -> 9.1 MiB (32%), verified`.
- `manifest`: path of the manifest that was written
- `engine`, `warnings`, `count(status)`
- `to_dict()` for JSON (`json.dumps(result.to_dict())`)

One failing file does not stop the others; check `result.status` or
`result.count(bv.Status.FAILED)`.

## Errors

Problems that stop a run before any work (bad input, damaged manifest, missing
engine, bad signature) raise exceptions that all derive from
`bv.BatchVaultError`:

`InputError`, `OptionsError`, `ManifestError`, `EngineNotFoundError`,
`EngineError`, `OutputExistsError`, `VolumeError`, `SignatureError`,
`ConfigError`.

```python
try:
    bv.unpack("archives", "restored", public_key="batchvault.pub")
except bv.SignatureError as exc:
    print("Do not trust this archive:", exc)
```

## Progress

Pass a callback to follow a run. It is called from worker threads, so keep it
thread-safe.

```python
def show(event: bv.ProgressEvent):
    print(event.kind, event.source.name, event.status)

bv.pack(["a.bin", "b.bin"], "archives", progress=show)
```

Events are `"plan"` (once, with the total size and file count), then `"start"`
and `"finish"` for each file.

## Logging

The library only writes to the `batchvault` logger, and adds no handlers. Send
it where you like:

```python
import logging
logging.getLogger("batchvault").addHandler(logging.StreamHandler())
logging.getLogger("batchvault").setLevel(logging.INFO)
```

## Examples

Only re-archive what changed, without ever deleting anything:

```python
result = bv.pack(bv_files, "archives", update=True)
print(result.count(bv.Status.SKIPPED), "files were up to date")
```

Restore only if the manifest is signed by you:

```python
bv.unpack("archives", "restored", public_key="batchvault.pub", password="...")
```

Free disk space safely, asking first:

```python
bv.pack(files, "archives", delete_source=True,
        confirm=lambda paths: input(f"Delete {len(paths)} files? ") == "y")
```
