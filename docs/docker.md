# Docker guide

Images are published to the GitHub Container Registry:

| Image | Contains |
|---|---|
| `ghcr.io/coldsofttech/batchvault:<version>` (and `:latest`) | BatchVault with the built-in engine, encryption and signing |
| `ghcr.io/coldsofttech/batchvault:<version>-7zip` (and `:latest-7zip`) | The same, plus 7-Zip |

Both support `linux/amd64` and `linux/arm64`. The 7-Zip variant installs 7-Zip
from the operating system's package; it is separate software under its own
license.

## Running

The container works in `/data`, so mount the folder you want to work in there:

```
docker run --rm -v "$PWD:/data" ghcr.io/coldsofttech/batchvault \
  pack report.pdf -o archives --volume-size 100m
```

```
docker run --rm -v "$PWD:/data" ghcr.io/coldsofttech/batchvault \
  unpack archives -o restored
```

Show the help:

```
docker run --rm ghcr.io/coldsofttech/batchvault --help
```

Use 7-Zip:

```
docker run --rm -v "$PWD:/data" ghcr.io/coldsofttech/batchvault:latest-7zip \
  pack report.pdf -o archives --engine 7zip
```

## Passwords and keys

Pass secrets through a mounted file or the environment, not on the command
line. A value given with `-e` can be seen by anyone who can run `docker inspect`
on the container, so a mounted password file (below) is safer:

```
docker run --rm -v "$PWD:/data" -e BATCHVAULT_PASSWORD \
  ghcr.io/coldsofttech/batchvault pack secrets.db -o vault --encrypt
```

```
docker run --rm -v "$PWD:/data" ghcr.io/coldsofttech/batchvault \
  unpack vault -o restored --password-file /data/pw.txt
```

## File ownership

The container runs as user ID 1000. On Linux, files it creates in the mounted
folder belong to that user. To keep your own ownership:

```
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD:/data" \
  ghcr.io/coldsofttech/batchvault pack report.pdf -o archives
```

## Logs and settings

Logs are written inside the container and disappear with it. To keep them,
mount a folder and point `BATCHVAULT_LOG_DIR` at it:

```
docker run --rm -v "$PWD:/data" -v "$PWD/logs:/logs" -e BATCHVAULT_LOG_DIR=/logs \
  ghcr.io/coldsofttech/batchvault pack report.pdf -o archives
```

A `batchvault.toml` in the mounted folder is picked up automatically.

## Building the image yourself

```
docker build -t batchvault .
docker build --build-arg WITH_7ZIP=true -t batchvault:7zip .
```
