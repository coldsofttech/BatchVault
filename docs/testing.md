# Testing

There are four layers. Each has a marker, a place in the repository and a
trigger.

| Layer | Where | Marker | What it proves |
|---|---|---|---|
| Unit | `tests/test_*.py` | none | Modules behave correctly, fast, no external tools |
| Integration | `tests/integration/` | `integration` | The 7-Zip engine and the API work with a real 7-Zip (skipped without it) |
| End-to-end | `tests/e2e/` | `e2e` | The installed program works the way users use it |
| End-to-end, GitHub Action | `.github/workflows/e2e.yml` (job `action`) | n/a | `action.yml` works on real runners |

Everything under `tests/e2e/` is marked `e2e` automatically. Two sub-markers
allow narrower runs: `package` (builds the wheel and installs it into a fresh
virtual environment) and `docker` (builds and runs the images).

## What each end-to-end suite covers

- **CLI** (`tests/e2e/test_cli_e2e.py`): runs the real `batchvault` command in a
  separate process. Full journey (list, pack, inspect, verify, unpack, byte
  comparison) on both engines, `--update`, standard-input pipelines,
  `--json-events`, exit codes, damaged archives, restoring from one volume,
  config files, parallel runs, log files, `--delete-source`, encryption and
  signing.
- **Python package** (`tests/e2e/test_package_e2e.py`): builds the wheel, checks
  its contents, installs it into a clean virtual environment, and checks the
  metadata, the console script, `python -m batchvault`, the API (and that
  importing it does not load `rich`), the message when the optional `crypto`
  extra is missing, and encryption and signing once it is installed.
- **Docker** (`tests/e2e/test_docker_e2e.py`): builds both images from the
  `Dockerfile` and runs containers with a mounted folder: default command,
  non-root user, pack and unpack, exit codes, encryption, config file, and 7-Zip
  only in the `-7zip` image.
- **GitHub Action** (workflow job `action`): uses the action from this checkout
  (`uses: ./`) on Linux, Windows and macOS: pack, verify, unpack, `update`,
  artifact upload and download, encryption with right and wrong passwords,
  signing and a tampered manifest, failure reporting, and 7-Zip where the
  runner has it. Assertions are shell and Python steps that compare outputs and
  restored bytes (`tests/e2e/scripts/`).

## Running them locally

```
uv run pytest -m "not integration and not e2e" -n auto   # unit tests (default, fast)
uv run pytest -m integration                             # needs 7-Zip
uv run pytest -m "e2e and not docker"                    # CLI + package (needs network for pip)
uv run pytest -m "e2e and not docker and not package"    # CLI only, quickest
uv run pytest -m package                                 # package only
uv run pytest -m docker                                  # needs Docker; builds the images
```

Do not use `-n` with the package tests; they share a wheel and environments.

To test images you already built or pulled, skip the build:

```
BATCHVAULT_E2E_IMAGE=ghcr.io/coldsofttech/batchvault:latest \
BATCHVAULT_E2E_IMAGE_7ZIP=ghcr.io/coldsofttech/batchvault:latest-7zip \
uv run pytest -m docker
```

The Action job cannot run under pytest. To try it locally, use
[act](https://github.com/nektos/act) (`act -j action`); Linux only, and the
artifact steps may need extra setup. Otherwise push a branch and run the
workflow by hand (below).

## Platforms in CI

Unit and integration tests (`tests.yml`) and the CLI and package end-to-end
tests (`e2e.yml`) use the same matrix: Ubuntu with Python 3.11, 3.12, 3.13, 3.14
and 3.15 (3.15 is allowed to fail while it may be a pre-release), Windows with
3.11 and macOS with 3.11. The Docker tests run on Ubuntu only, and the Action
tests run on Ubuntu, Windows and macOS with Python 3.12.

## When they run in CI

| Trigger | Unit | Integration | CLI + package | Docker | Action |
|---|---|---|---|---|---|
| Pull request | yes | yes | if `src/`, `tests/e2e/`, `action.yml`, `Dockerfile`, `pyproject.toml` changed | same | same |
| Push to `main` | yes | yes | yes | yes | yes |
| Weekly schedule (Mondays) | no | no | yes | yes | yes |
| Manual (**Actions** tab, **End-to-end**, **Run workflow**) | no | no | yes | yes | yes |
| Release tag (`v*.*.*`) | yes | yes | yes | yes | yes, and publishing waits for it |

Unit and integration tests run from `tests.yml`; end-to-end suites run from
`e2e.yml`, which `release.yml` calls before it publishes anything.

## Adding a test

- Behaviour of one module: a unit test in `tests/`.
- Something a user would notice when running the program: add it to
  `tests/e2e/test_cli_e2e.py`.
- Something about packaging or installation: `test_package_e2e.py`.
- Something that only exists inside the image: `test_docker_e2e.py`.
- A new Action input: add a step to the `action` job in `e2e.yml` that uses it
  and asserts on the outputs or files.

Keep end-to-end tests independent: each uses its own `tmp_path` and a private
log folder, and never touches your real archives or log folder.
