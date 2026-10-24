# Configuration

BatchVault reads defaults from `batchvault.toml`, or from a `[tool.batchvault]`
table in `pyproject.toml`. It looks in the current folder and each parent, or
use `--config FILE` / `BATCHVAULT_CONFIG`. Precedence: command line, config
file, built-in defaults. `include` and `exclude` lists from the file and the
command line are combined. An on/off option set to `true` in the file cannot be
switched off from the command line.

## Trusting a config file

A config file found by searching the current folder and its parents might belong
to someone else (a cloned repository, a downloaded folder). So a file found this
way **cannot** set options that run programs, send data out, choose trusted keys,
read passwords or delete files:

`seven-zip`, `notify` (and `[notify] url`), `delete-source`, `yes`, `public-key`,
`sign-key`, `password-file`, and `log-file` (and `[log] file`).

Using one of them in such a file is an error that tells you how to proceed. If you
wrote the file and trust it, name it yourself, which counts as trusting it:

```
batchvault pack --list files.txt --config batchvault.toml
```

or set `BATCHVAULT_CONFIG` to its path. Everything else (levels, volume size,
engine, filters, retries, `update`, ...) works from a found file as usual.

## Keys

Keys use the option names (dashes or underscores): `volume-size` for
`--volume-size`. Unknown sections, keys or invalid values are errors (exit
code 2).

```toml
[defaults]            # every command that has the option
jobs = 4
seven-zip = "/usr/bin/7zz"   # --7z; only allowed with --config (see "Trusting a config file")

[log]
level = "DEBUG"       # --log-level
file = "run.log"      # --log-file

[notify]
url = "https://hooks.example.com/..."   # --notify
on = "failure"                          # failure, success or always

[pack]                # pack, unpack, verify and inspect have their own tables
engine = "native"
level = 7
volume-size = "100m"
include = ["*.mp4"]
exclude = ["tmp/"]
update = true
retries = 2

[unpack]
update = true
```

Never put passwords in the file. Use `BATCHVAULT_PASSWORD`,
`--password-file`, and `BATCHVAULT_KEY_PASSWORD` for an encrypted signing key.
A notification URL often contains a secret; prefer `BATCHVAULT_NOTIFY_URL`.

## Environment variables

| Variable | Meaning |
|---|---|
| `BATCHVAULT_CONFIG` | Config file path |
| `BATCHVAULT_7Z` | 7-Zip executable |
| `BATCHVAULT_PASSWORD` | Archive password (used by `--encrypt`, `unpack`, `verify`) |
| `BATCHVAULT_KEY_PASSWORD` | Password of the signing private key |
| `BATCHVAULT_NOTIFY_URL` | Webhook URL for `--notify` |
| `BATCHVAULT_LOG_DIR` | Folder for log files |
