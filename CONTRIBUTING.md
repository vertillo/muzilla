# Contributing to muzilla

## Development setup

Backend:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,audio]"
pre-commit install
```

`[audio]` is required, not optional: it pulls in `pillow` and `pyacoustid`, both exercised by
non-optional code paths (art embedding, fingerprinting). `.[dev]` alone leaves those modules
unavailable.

### Environment variables for a local run

The packaged defaults in `src/muzilla/config/defaults.yaml` are *container* paths — `library_root: /music`, `data_dir: /data`. Neither exists on a development machine and `/data` is not writable, so `muzilla serve` needs local overrides before it will start. Auth is mandatory as well (`auth.enabled` defaults to `true`), which is what the last two variables cover.

```bash
export MUZILLA_STORAGE__LIBRARY_ROOT="$PWD/dev-library"
export MUZILLA_STORAGE__DATA_DIR="$PWD/data"
export MUZILLA_STORAGE__DB_PATH="$PWD/data/muzilla.db"
export MUZILLA_STORAGE__CACHE_DIR="$PWD/data/cache"
export MUZILLA_STORAGE__BLOB_DIR="$PWD/data/blobs"
export MUZILLA_AUTH__PASSWORD="$(openssl rand -base64 36)"
export MUZILLA_AUTH__SESSION_SECRET="$(openssl rand -base64 36)"

mkdir -p "$PWD/dev-library" "$PWD/data"
muzilla serve --reload
```

`db_path`, `cache_dir` and `blob_dir` do **not** derive from `data_dir` — each is an independent absolute default. Setting only `MUZILLA_STORAGE__DATA_DIR` leaves the other three at `/data/...`: `db_path` fails loudly because migrations run at startup, but a stale `cache_dir` or `blob_dir` only breaks at the first provider fetch or art write, long after the app looks healthy.

`dev-library/` and `data/` are both gitignored. Put a few throwaway audio files in `dev-library/` — not a copy of your real library, and not `music/fixtures/`, since applying a change rewrites tags in place.

Rather than exporting seven variables every session, the same keys can go in a YAML file with `MUZILLA_CONFIG_DIR` pointing at its directory: the loader reads `$MUZILLA_CONFIG_DIR/config.yaml` (`src/muzilla/config/loader.py`), which is how the Playwright harness configures its runs (`e2e/tests/fixtures.ts`).

`MUZILLA_AUTH__ENABLED=false` skips the password entirely. That is fine on loopback; never use it on anything reachable from the network.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Run the full stack in Docker:

```bash
docker compose up --build
```

## Project layout

Read [`AGENTS.md`](AGENTS.md), the
[`product specification`](docs/product-spec.md), the
[`completion matrix`](docs/completion-matrix.md), and the
[`production-readiness contract`](docs/production-readiness.md). In short:

- `src/muzilla/domain/` — pure data model, no I/O. Everything else derives from `domain/fields.py`.
- `src/muzilla/services/` — the only layer the API and CLI are allowed to import. If you're adding a feature reachable from both, it belongs here.
- `frontend/src/components/ui/` — design-system primitives ported from the Claude Design project. Keep them thin; app logic belongs in `pages/`.

An `import-linter` contract in CI enforces the layering rules (`api`/`cli` → `services` only). Run it locally with `lint-imports`.

## Before opening a PR

```bash
# backend
ruff check src tests
mypy src
lint-imports
pytest -q

# frontend
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build

# browser acceptance: build first, because Playwright serves packaged static assets
cd ../e2e
npm run test

# container acceptance (isolated temporary bind/volume; never use a user library)
cd ..
docker build -f docker/Dockerfile -t muzilla:candidate .
MUZILLA_TEST_IMAGE=muzilla:candidate .venv/bin/python tests/container/compose_smoke.py
MUZILLA_TEST_IMAGE=muzilla:candidate .venv/bin/python tests/container/backup_restore_smoke.py
```

The release smoke must run against the exact candidate image reference. It verifies native
runtime, readiness and isolated Compose behavior, then archives `/data` from a stopped
container, checks the archive SHA-256, and restores only into a fresh empty volume. The music
bind mount and external configuration/secrets are excluded. Browser fixtures use temporary
directories and must tear them down after each run. Do not encode an old test count as a
current readiness claim.

Changes to reset, delete, secret cleanup or storage containment must start with a failing
test that uses only `tmp_path`/a temporary Docker volume. Assert the music fixture's hash
(and inode where meaningful) before and after, keep the library bind read-only in the
Compose acceptance, and cover restart with the same named `/data` volume. Never point a
reset test at `music/`, `data/`, an `.env` path or a developer/user library. A green test
count is not sufficient: include negative Origin/CSRF/re-auth, maintenance-lock,
idempotency, symlink/overlap and partial-cleanup recovery assertions.

When retiring legacy UI/API, first add coverage for the ReviewBundle replacement journey and
then delete the route, schema/client and component together. Do not treat a wildcard fallback
or a redirect as replacement coverage. ChangeSet is pre-production legacy surface: no
permanent compatibility or preservation of its application state is required. Keep
`COMPAT-CHANGESET-001` open until the final adapter and compatibility surface are removed.

## Commit style

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, etc.) for changelog generation.

## Reporting issues

Open a GitHub issue. For anything touching metadata matching or tag writing, include a minimal reproduction (a fixture file or the exact tags involved) — those subsystems are the highest-risk code in the project and need precise repro steps.
