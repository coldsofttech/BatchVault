# GitHub Action guide

```yaml
- uses: coldsofttech/BatchVault@v1
  with:
    command: pack
    files: |
      reports/summary.pdf
      data/export.csv
    output: archives
```

The action installs BatchVault, runs one command, writes a per-file table to the
job summary, and fails the step if anything failed or was skipped (turn that off
with `fail-on-error: false`).

By default it installs the version that matches the action's own ref
(`@v1`, a tag or a commit). Set `version` to install a specific release from
PyPI instead.

## Inputs

| Input | Used by | Meaning | Default |
|---|---|---|---|
| `command` | all | `pack`, `unpack` or `verify` | `pack` |
| `files` | pack | Files to archive, one path per line | |
| `list-file` | pack | Text or JSON list of files | |
| `source` | unpack, verify | Manifest, folder, volume or list | |
| `output` | pack, unpack | Output folder | `archives` / `restored` |
| `engine` | pack | `auto`, `native` or `7zip` | `auto` |
| `format` | pack | `xz`, `gzip`, `bzip2`, `store` (built-in engine) | |
| `level` | pack | Compression level 0-9, or `auto` to pick one for each file from its type | `auto` |
| `volume-size` | pack | e.g. `500k`, `10m`, `2g` | `10m` |
| `jobs` | all | Worker threads; `0` means none, one file at a time | `0` |
| `include`, `exclude` | pack | Globs, one per line | |
| `root` | pack | Advanced: store paths relative to this folder instead of the automatic choice (folders are kept either way) | automatic |
| `force` | pack, unpack | Replace existing archives / files | `false` |
| `update` | pack, unpack | Skip unchanged files | `false` |
| `verify` | pack | Test each archive after creating it | `false` |
| `retries` | pack, unpack | Retries after an I/O or engine error | `3` |
| `manifest` | pack | Manifest location | |
| `against` | verify | Folder of originals to compare with | |
| `encrypt` | pack | Encrypt the archives (needs `password`) | `false` |
| `password` | pack, unpack, verify | Archive password; use a secret | |
| `sign-key` | pack | Private key path for signing the manifest | |
| `public-key` | unpack, verify | Require a valid manifest signature | |
| `seven-zip` | all | Path to a 7-Zip executable | |
| `version` | all | BatchVault version from PyPI | matches the action ref |
| `python-version` | all | Python used to run BatchVault | `3.12` |
| `upload-artifact` | all | Upload the output folder as an artifact | `false` |
| `artifact-name` | all | Name of that artifact | `batchvault` |
| `fail-on-error` | all | Fail the step on any failure or skip | `true` |

## Outputs

| Output | Meaning |
|---|---|
| `status` | `success`, `failed`, `skipped` or `partial` |
| `result-json` | Path of the JSON result file |
| `output` | The output folder that was used |

## Examples

Archive build output in 100 MB volumes and keep it as an artifact:

```yaml
- uses: coldsofttech/BatchVault@v1
  with:
    command: pack
    files: |
      build/app.zip
      build/symbols.zip
    volume-size: 100m
    verify: true
    upload-artifact: true
    artifact-name: release-archives
```

Archive everything under a folder, except logs (using a list file):

```yaml
- name: List files
  run: |
    pip install batchvault
    batchvault list files data --exclude "*.log" -o files.json

- uses: coldsofttech/BatchVault@v1
  with:
    list-file: files.json
    output: archives
```

Encrypt with a password from a repository secret:

```yaml
- uses: coldsofttech/BatchVault@v1
  with:
    files: secrets/database.dump
    encrypt: true
    password: ${{ secrets.ARCHIVE_PASSWORD }}
```

Restore, refusing a manifest that was not signed with your key:

```yaml
- uses: coldsofttech/BatchVault@v1
  with:
    command: unpack
    source: archives
    output: restored
    public-key: keys/batchvault.pub
    password: ${{ secrets.ARCHIVE_PASSWORD }}
```

Use the result in a later step:

```yaml
- id: vault
  uses: coldsofttech/BatchVault@v1
  with:
    files: data.db
    fail-on-error: false

- if: steps.vault.outputs.status != 'success'
  run: echo "Archiving did not fully succeed: ${{ steps.vault.outputs.status }}"
```

## Notes

- Inputs reach the command through environment variables and an argument list,
  never through shell interpolation, so a value cannot inject commands. File
  paths are passed after `--`, so a path that starts with `-` is never read as
  an option, even if it comes from untrusted data.
- The `password` input is masked in the log.
- GitHub-hosted runners usually include 7-Zip, so `engine: auto` may pick it.
  Set `engine: native` for identical behaviour on every runner.
- Pin `@v1` (a moving tag), a full version tag, or a commit SHA, depending on how
  much stability you need.
