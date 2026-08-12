# ---- Stage 1: build the frontend ----
# Deliberately use Docker's bundled Dockerfile frontend.  Requiring
# `docker/dockerfile:1` made an otherwise local release gate depend on a remote
# frontend pull; the supported BuildKit syntax used below is available here.
# Vite's outDir (../src/muzilla/web/static, see frontend/vite.config.ts) is
# relative to the frontend/ workdir, so the sibling src/ tree must exist
# under the same /app root for the build output to land where we expect it.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/
# --legacy-peer-deps: openapi-typescript declares a
# stale `^5.x` TypeScript peer dependency against this repo's TypeScript
# 6; it generates and typechecks correctly regardless, but plain `npm ci`
# enforces peer deps strictly and fails outright without this flag. Found by
# actually running `docker compose up --build` from a fresh clone — local
# development had already needed the same flag for `npm install`, but
# the Dockerfile's `npm ci` call was never updated to match.
RUN --mount=type=cache,target=/root/.npm \
    cd frontend && npm ci --legacy-peer-deps
COPY frontend/ frontend/
RUN cd frontend && npm run build

# ---- Stage 2: build the pinned rsgain release ----
# Keep the compiler and development headers out of the runtime stage.  Pin
# the Debian suite because the explicit runtime package names below are a
# distro contract; an implicit suite rollover can otherwise change SONAMEs
# underneath an unchanged Dockerfile.
FROM python:3.12-slim-trixie AS rsgain-build

# rsgain has no Debian package. The upstream GitHub releases don't actually
# publish a .deb asset (only a source tarball) — verified against the
# project's own Homebrew formula (source-only). So this builds the pinned source
# release instead, with the checksum Homebrew's formula pins for it.
# TODO: if upstream ever ships a .deb, switch to installing that
# directly. NOTE: pinned to v3.4, the
# version this checksum was verified against (via the local
# complexlogic/tap Homebrew formula) — bump both together, never the
# version alone, since a stale checksum against a newer tarball will
# just fail the sha256sum check loudly rather than silently.
ARG RSGAIN_VERSION=3.4
ARG RSGAIN_SOURCE_SHA256=392ad1407eea7737c6f63b6a0681a3df51f33033d2d16b644f407e6ef3f28013
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        curl xz-utils cmake pkg-config build-essential \
        libavformat-dev libavcodec-dev libavutil-dev libswresample-dev \
        libebur128-dev libtag1-dev libinih-dev libfmt-dev; \
    curl -fsSL -o /tmp/rsgain-src.tar.xz \
        "https://github.com/complexlogic/rsgain/releases/download/v${RSGAIN_VERSION}/rsgain-${RSGAIN_VERSION}-source.tar.xz"; \
    echo "${RSGAIN_SOURCE_SHA256}  /tmp/rsgain-src.tar.xz" | sha256sum -c -; \
    mkdir /tmp/rsgain-src && tar -xJf /tmp/rsgain-src.tar.xz -C /tmp/rsgain-src --strip-components=1; \
    cmake -S /tmp/rsgain-src -B /tmp/rsgain-build -DCMAKE_BUILD_TYPE=Release; \
    cmake --build /tmp/rsgain-build -j"$(nproc)"; \
    cmake --install /tmp/rsgain-build; \
    rsgain --version; \
    rm -rf /tmp/rsgain-src /tmp/rsgain-src.tar.xz /tmp/rsgain-build; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 3: Python runtime ----
FROM python:3.12-slim-trixie AS runtime

# These are runtime packages, not development headers.  Keep them explicit:
# rsgain links to each SONAME directly and apt's autoremove cannot infer that
# dependency from a binary compiled outside Debian's package system.
# The build arg exists solely so the negative container test can build this
# exact Dockerfile without TagLib and prove that the assertion below rejects
# the image. Production builds keep the default.
ARG RSGAIN_TAGLIB_RUNTIME_PACKAGE=libtag2
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libchromaprint-tools \
    libebur128-1 \
    libfmt10 \
    libinih1 \
    ${RSGAIN_TAGLIB_RUNTIME_PACKAGE} \
    && rm -rf /var/lib/apt/lists/*

COPY --from=rsgain-build /usr/local/bin/rsgain /usr/local/bin/rsgain

# Fail the image build at the final runtime boundary, where the package set
# exactly matches what will be distributed.  Checking only in the builder
# would reproduce the original false positive.
RUN set -eux; \
    ldd /usr/local/bin/rsgain | tee /tmp/rsgain-ldd; \
    ! grep -F "not found" /tmp/rsgain-ldd; \
    rsgain --version; \
    rm /tmp/rsgain-ldd

RUN useradd --create-home --uid 1000 muzilla
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./

# Bring in the frontend build output (Vite wrote it to src/muzilla/web/static
# under the frontend-build stage's /app root — see frontend/vite.config.ts)
# BEFORE `pip install`, since hatchling snapshots the package contents at
# install time — installing first would ship an empty static/ directory.
COPY --from=frontend-build /app/src/muzilla/web/static ./src/muzilla/web/static

RUN pip install --no-cache-dir ".[audio]"

RUN mkdir -p /data /music && chown -R muzilla:muzilla /data /app
USER muzilla

ENV MUZILLA_STORAGE__DATA_DIR=/data \
    MUZILLA_STORAGE__DB_PATH=/data/muzilla.db \
    MUZILLA_STORAGE__CACHE_DIR=/data/cache \
    MUZILLA_STORAGE__LIBRARY_ROOT=/music

VOLUME ["/data", "/music"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/ready', timeout=3).status==200 else 1)"

ENTRYPOINT ["muzilla"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080", "--forwarded-allow-ips", "127.0.0.1"]
