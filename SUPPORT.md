# Support

- **Start with the docs:** the [README](README.md) covers installing and using BatchVault. For more detail see the [command line](docs/guides/cli.md), the [Python API](docs/guides/python.md), [configuration](docs/guides/configuration.md), [compression](docs/guides/compression.md), [Docker](docs/guides/docker.md) and the [GitHub Action](docs/guides/github-action.md).
- **Check your setup:** run `batchvault doctor`. It reports your Python version, whether the xz, gzip and bzip2 libraries and 7-Zip are available, and where logs are kept.
- **Questions and ideas:** use [GitHub Discussions](../../discussions) if enabled, otherwise open an issue with the feature request form.
- **Bugs:** open an issue with the bug report form. Include the BatchVault version (`batchvault --version`), the engine (native or 7-Zip), your operating system and Python version, how you run it (command line, Python, Docker or the GitHub Action), the command or code, and the error output. The newest run log helps too; `batchvault doctor` shows where they are kept. Leave out passwords, key files and private data.
- **Security problems:** see [SECURITY.md](SECURITY.md). Do not open a public issue.
