# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend ----
# Vite's outDir (../src/muzilla/web/static, see frontend/vite.config.ts) is
# relative to the frontend/ workdir, so the sibling src/ tree must exist
# under the same /app root for the build output to land where we expect it.
FROM node:22-slim AS frontend-build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/
RUN --mount=type=cache,target=/root/.npm \
    cd frontend && npm ci
COPY frontend/ frontend/
RUN cd frontend && npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

# rsgain has no Debian package. docs/PLAN.md's native-dependency table
# calls for "install pinned .deb from GitHub releases with checksum",
# but complexlogic/rsgain's GitHub releases don't actually publish a
# .deb asset (only a source tarball) — verified against the project's
# own Homebrew formula (source-only). So this builds the pinned source
# release instead, with the checksum Homebrew's formula pins for it,
# and removes the build toolchain in the same layer to keep the
# runtime image lean.
# TODO: if upstream ever ships a .deb, switch to installing that
# directly per the plan's original intent. NOTE: pinned to v3.4, the
# version this checksum was verified against (via the local
# complexlogic/tap Homebrew formula) — bump both together, never the
# version alone, since a stale checksum against a newer tarball will
# just fail the sha256sum check loudly rather than silently.
ARG RSGAIN_VERSION=3.4
ARG RSGAIN_SOURCE_SHA256=392ad1407eea7737c6f63b6a0681a3df51f33033d2d16b644f407e6ef3f28013
RUN set -eux; \
    BUILD_DEPS="curl xz-utils cmake pkg-config build-essential libavformat-dev libavcodec-dev libavutil-dev libebur128-dev libtag1-dev"; \
    apt-get update && apt-get install -y --no-install-recommends $BUILD_DEPS; \
    curl -fsSL -o /tmp/rsgain-src.tar.xz \
        "https://github.com/complexlogic/rsgain/releases/download/v${RSGAIN_VERSION}/rsgain-${RSGAIN_VERSION}-source.tar.xz"; \
    echo "${RSGAIN_SOURCE_SHA256}  /tmp/rsgain-src.tar.xz" | sha256sum -c -; \
    mkdir /tmp/rsgain-src && tar -xJf /tmp/rsgain-src.tar.xz -C /tmp/rsgain-src --strip-components=1; \
    cmake -S /tmp/rsgain-src -B /tmp/rsgain-build -DCMAKE_BUILD_TYPE=Release; \
    cmake --build /tmp/rsgain-build -j"$(nproc)"; \
    cmake --install /tmp/rsgain-build; \
    rm -rf /tmp/rsgain-src /tmp/rsgain-src.tar.xz /tmp/rsgain-build; \
    apt-get purge -y --auto-remove $BUILD_DEPS; \
    rm -rf /var/lib/apt/lists/*

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
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3).status==200 else 1)"

ENTRYPOINT ["muzilla"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
