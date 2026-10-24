# Development

## Setup

```
uv sync
uv run pre-commit install
```

## Common tasks

```
uv run pytest -m "not integration and not e2e" -n auto   # unit tests
uv run pytest -m integration                             # needs 7-Zip
uv run pytest -m "e2e and not docker"                    # CLI and package, end to end
uv run pytest -m docker                                  # needs Docker
uv run ruff check .                                      # lint
```

See [testing.md](testing.md) for what each layer covers and when CI runs it.

## Releasing

1. Update the version in `pyproject.toml` and `src/batchvault/__init__.py`.
2. Move the `[Unreleased]` changelog entries under the new version.
3. Push a tag `vX.Y.Z`. The release workflow checks the versions, runs lint and
   tests, publishes to PyPI via trusted publishing, and creates a GitHub release.

PyPI trusted publishing needs the repository and the `pypi` environment
registered as a trusted publisher in the PyPI project settings.
