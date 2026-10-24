# Contributing

Thanks for helping improve BatchVault.

## Setup

```
uv sync
uv run pre-commit install
```

## Before you open a pull request

```
uv run ruff check .
uv run pytest -m "not integration and not e2e" -n auto
uv run pytest -m integration        # needs 7-Zip installed; skipped otherwise
uv run pytest -m "e2e and not docker"   # optional: end-to-end CLI and package tests
```

CI also runs the Docker and GitHub Action end-to-end tests; see
[docs/testing.md](docs/testing.md).

- Add or update tests for the behaviour you change.
- Add a one or two line entry to `[Unreleased]` in `CHANGELOG.md`, written for
  users (no file or function names).
- User-facing documentation goes in `README.md`; technical notes go in `docs/`.
- Keep the library free of `rich` imports; terminal output belongs in `ui.py`
  and `cli.py`.

## Reporting problems

Use the issue templates. For security problems, see [SECURITY.md](SECURITY.md)
instead of opening a public issue.
