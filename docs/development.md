# Development

## Setup

```
uv sync
uv run pre-commit install
```

The layout of the code is described in [architecture.md](architecture.md). Tests live in
`tests/`, with `tests/unit/` mirroring `src/batchvault/`; see [Testing](#testing) below.

## Common tasks

```
uv run pytest -m "not integration and not e2e" -n auto   # unit tests
uv run pytest -m integration                             # native always; 7-Zip tests need 7-Zip
uv run pytest -m "e2e and not docker"                    # CLI and package, end to end
uv run pytest -m docker                                  # needs Docker
uv run ruff check .                                      # lint
uv run mypy                                              # type check
```

`sweepito.toml` configures Sweepito for this repository, if you use it to clean caches and
build output (`.venv/` is kept).

## Imports

Imports are lazy: a module imports what it needs inside the function that uses it, and names
used only in annotations sit under `TYPE_CHECKING`. A name needed when the module loads (a
base class, a compiled regular expression, a module constant) stays at the top of its module.
The public names in `batchvault/__init__.py` are loaded on first use through `_LAZY` and
`__getattr__`; add a new public name to `_LAZY`, `__all__` and the `TYPE_CHECKING` block.

## Data files

Two kinds of content are kept out of the code and read on first use:

- `src/batchvault/core/data/levels.toml`: which compression level `--level auto` picks for
  each kind of file. Each group has a `kind`, a `level` (0 to 9) and its file extensions; the
  `incompressible` group must stay, because a compressed-file signature always gets its level.
- `src/batchvault/archive/templates/*.md.tmpl`: the text of the `BATCHVAULT-README.md` written
  next to a manifest. They are `string.Template` files, so a missing placeholder is an error.

Both are packaged with the wheel.

## Testing

There are four layers. Each has a marker, a place in the repository and a trigger.

| Layer | Where | Marker | What it proves |
|---|---|---|---|
| Unit | `tests/unit/` (mirrors `src/batchvault/`) | none | Modules behave correctly, fast, no external tools |
| Integration | `tests/integration/native/`, `tests/integration/sevenzip/` | `integration` | Each engine and the API work end to end on larger, multi-volume data; the 7-Zip ones need a real 7-Zip (skipped without it) |
| End-to-end | `tests/e2e/` | `e2e` | The installed program works the way users use it |
| End-to-end, GitHub Action | `.github/workflows/e2e.yml` (job `action`) | n/a | `action.yml` works on real runners |

Everything under `tests/e2e/` is marked `e2e` automatically. Two sub-markers allow narrower
runs: `package` (builds the wheel and installs it into a fresh virtual environment) and
`docker` (builds and runs the images).

### What each end-to-end suite covers

- **CLI** (`tests/e2e/test_cli_e2e.py`): runs the real `batchvault` command in a separate
  process. Full journey (list, pack, inspect, verify, unpack, byte comparison) on both
  engines, `--update`, standard-input pipelines, `--json-events`, exit codes, damaged
  archives, restoring from one volume, config files, parallel runs, log files,
  `--delete-source`, encryption and signing.
- **Python package** (`tests/e2e/test_package_e2e.py`): builds the wheel, checks its
  contents, installs it into a clean virtual environment, and checks the metadata, the
  console script, `python -m batchvault`, the API (and that importing it does not load
  `rich`), the message when the optional `crypto` extra is missing, and encryption and
  signing once it is installed.
- **Docker** (`tests/e2e/test_docker_e2e.py`): builds both images from the `Dockerfile` and
  runs containers with a mounted folder: default command, non-root user, pack and unpack,
  exit codes, encryption, config file, and 7-Zip only in the `-7zip` image.
- **GitHub Action** (workflow job `action`): uses the action from this checkout (`uses: ./`)
  on Linux, Windows and macOS: pack, verify, unpack, `update`, artifact upload and download,
  encryption with right and wrong passwords, signing and a tampered manifest, failure
  reporting, and 7-Zip where the runner has it. Assertions are shell and Python steps that
  compare outputs and restored bytes (`tests/e2e/scripts/`).

### Running them locally

```
uv run pytest -m "not integration and not e2e" -n auto   # unit tests (default, fast)
uv run pytest -m integration                             # 7-Zip tests need 7-Zip
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
[act](https://github.com/nektos/act) (`act -j action`); Linux only, and the artifact steps may
need extra setup. Otherwise push a branch and run the workflow by hand (below).

### Platforms in CI

Unit and integration tests (`tests.yml`) and the CLI and package end-to-end tests (`e2e.yml`)
use the same matrix: Ubuntu with Python 3.11, 3.12, 3.13, 3.14 and 3.15 (3.15 is allowed to
fail while it may be a pre-release), Windows with 3.11 and macOS with 3.11. The Docker tests
run on Ubuntu only, and the Action tests run on Ubuntu, Windows and macOS with Python 3.12.

### When they run in CI

| Trigger | Unit | Integration | CLI + package | Docker | Action |
|---|---|---|---|---|---|
| Pull request | yes | yes | if `src/`, `tests/e2e/`, `action.yml`, `Dockerfile`, `pyproject.toml` changed | same | same |
| Push to `main` | yes | yes | yes | yes | yes |
| Weekly schedule (Mondays) | no | no | yes | yes | yes |
| Manual (**Actions** tab, **End-to-end**, **Run workflow**) | no | no | yes | yes | yes |
| Release tag (`v*.*.*`) | yes | yes | yes | yes | yes, and publishing waits for it |

Unit and integration tests run from `tests.yml`; end-to-end suites run from `e2e.yml`, which
`release.yml` calls before it publishes anything.

### Adding a test

- Behaviour of one module: a unit test in `tests/unit/`, in the folder that matches the
  module (`core/`, `core/api/`, `archive/`, `engines/` or `cli/`).
- An engine against real data or a real tool: `tests/integration/native/` or
  `tests/integration/sevenzip/`, marked `integration`.
- Something a user would notice when running the program: add it to
  `tests/e2e/test_cli_e2e.py`.
- Something about packaging or installation: `test_package_e2e.py`.
- Something that only exists inside the image: `test_docker_e2e.py`.
- A new Action input: add a step to the `action` job in `e2e.yml` that uses it and asserts on
  the outputs or files.

Keep end-to-end tests independent: each uses its own `tmp_path` and a private log folder, and
never touches your real archives or log folder.

## Releasing

1. Choose the version with [Semantic Versioning](https://semver.org/): a patch
   (`0.1.1`) for fixes only, a minor (`0.2.0`) for new features, a major for breaking changes
   to the public API or the command line. Before 1.0, a minor may also carry breaking changes;
   say so under `### Changed`. Update the version in `pyproject.toml` and
   `src/batchvault/__init__.py`.
2. Move the `[Unreleased]` changelog entries under the new version, keeping the `### Added`,
   `### Changed`, `### Deprecated`, `### Removed`, `### Fixed` and `### Security` headings that
   are used.
3. Push a tag `vX.Y.Z`. The release workflow checks the versions, runs lint and
   tests, publishes to PyPI via trusted publishing, and creates a GitHub release.

PyPI trusted publishing needs the repository and the `pypi` environment
registered as a trusted publisher in the PyPI project settings.
