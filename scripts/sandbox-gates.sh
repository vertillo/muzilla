#!/usr/bin/env bash
set -Eeuo pipefail

# Compose keeps these defaults for `muzilla serve`. Each gate command gets a clean
# application environment instead, especially the E2E fixture's config.yaml.
gate_env=(
    env
    -u MUZILLA_CONFIG_DIR
    -u MUZILLA_AUTH__ENABLED
    -u MUZILLA_AUTH__PASSWORD
    -u MUZILLA_AUTH__SESSION_SECRET
    -u MUZILLA_AUTH__COOKIE_SECURE
    -u MUZILLA_STORAGE__LIBRARY_ROOT
    -u MUZILLA_STORAGE__DATA_DIR
    -u MUZILLA_STORAGE__DB_PATH
    -u MUZILLA_STORAGE__CACHE_DIR
    -u MUZILLA_STORAGE__BLOB_DIR
    -u MUZILLA_STORAGE__BACKUP_DIR
    -u MUZILLA_STORAGE__PROVIDER_SECRETS_DIR
    -u MUZILLA_PROVIDERS__MUSICBRAINZ__TOKEN
    -u MUZILLA_PROVIDERS__DISCOGS__TOKEN
    -u MUZILLA_PROVIDERS__DEEZER__TOKEN
    -u MUZILLA_PROVIDERS__ACOUSTID__TOKEN
    -u MUZILLA_PROVIDERS__COVERARTARCHIVE__TOKEN
    -u MUZILLA_PROVIDERS__LRCLIB__TOKEN
    -u MUZILLA_PROVIDERS__MUSICBRAINZ__BASE_URL_OVERRIDE
    -u MUZILLA_PROVIDERS__DISCOGS__BASE_URL_OVERRIDE
    -u MUZILLA_PROVIDERS__DEEZER__BASE_URL_OVERRIDE
    -u MUZILLA_PROVIDERS__ACOUSTID__BASE_URL_OVERRIDE
    -u MUZILLA_PROVIDERS__COVERARTARCHIVE__BASE_URL_OVERRIDE
    -u MUZILLA_PROVIDERS__LRCLIB__BASE_URL_OVERRIDE
    -u MUSICBRAINZ_TOKEN
    -u DISCOGS_TOKEN
    -u DEEZER_TOKEN
    -u ACOUSTID_KEY
    -u ACOUSTID_API_KEY
    -u LRCLIB_TOKEN
)
run_gate() { "${gate_env[@]}" "$@"; }

run_gate uv run ruff check src tests
run_gate uv run mypy src
run_gate uv run lint-imports
run_gate uv run pytest -q --cov=muzilla --cov-report=term-missing

migration_db="/tmp/muzilla-alembic-check.$$.db"
trap 'rm -f "$migration_db"' EXIT
"${gate_env[@]}" "MUZILLA_ALEMBIC_DB_PATH=$migration_db" uv run alembic upgrade head
"${gate_env[@]}" "MUZILLA_ALEMBIC_DB_PATH=$migration_db" uv run alembic check

(
    cd frontend
    run_gate npm run lint
    run_gate npm run typecheck
    run_gate npm run test
    run_gate npm run build
)
(
    cd e2e
    run_gate npm run test
)

gate_image="${SANDBOX_GATE_IMAGE:-muzilla-sandbox-prod-gate:local}"
docker build --label com.muzilla.sandbox=true \
    --target runtime -f docker/Dockerfile -t "$gate_image" .
if docker history --no-trunc "$gate_image" | grep -E 'GITHUB_TOKEN|GH_TOKEN'; then
    printf 'GITHUB_TOKEN appeared in the candidate image history.\n' >&2
    exit 1
fi
docker image rm "$gate_image" >/dev/null
printf 'sandbox-gates: all project gates passed.\n'
