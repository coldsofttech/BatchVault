FROM python:3.13-slim AS build

WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build \
    && python -m build --wheel --outdir /dist


FROM python:3.13-slim

# Build with --build-arg WITH_7ZIP=true to add the 7-Zip engine. 7-Zip is
# LGPL software installed from the Debian package; it is not part of BatchVault.
ARG WITH_7ZIP=false
RUN if [ "$WITH_7ZIP" = "true" ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends 7zip \
        && rm -rf /var/lib/apt/lists/*; \
    fi

COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[crypto]" && rm /tmp/*.whl

RUN useradd --create-home --uid 1000 batchvault
USER batchvault

# Mount your files here, e.g. docker run --rm -v "$PWD:/data" batchvault ...
WORKDIR /data

LABEL org.opencontainers.image.title="BatchVault" \
      org.opencontainers.image.source="https://github.com/coldsofttech/BatchVault" \
      org.opencontainers.image.licenses="MIT"

ENTRYPOINT ["batchvault"]
CMD ["--help"]
