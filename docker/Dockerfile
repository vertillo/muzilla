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
    && rm -rf /var/lib/apt/lists/*

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

RUN pip install --no-cache-dir .

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
