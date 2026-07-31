# Contributing to muzilla

## Development setup

Backend:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,audio]"
pre-commit install
```

`[audio]` is required, not optional: it pulls in `pillow` and `pyacoustid`, both exercised by non-optional code paths (art embedding, fingerprinting). `.[dev]` alone leaves those unimportable — this was hit live on a fresh checkout following this exact instruction before `[audio]` was added here.

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

See [`docs/README.md`](docs/README.md) for the documentation map and
[`AGENTS.md`](AGENTS.md) for current repository invariants. The recovery architecture is
in [`docs/recovery-plan.md`](docs/recovery-plan.md); `docs/PLAN.md` documents the legacy
implementation and is no longer normative. In short:

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
npm run build
```

## Commit style

This repo uses [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, etc.) for changelog generation.

## Reporting issues

Open a GitHub issue. For anything touching metadata matching or tag writing, include a minimal reproduction (a fixture file or the exact tags involved) — those subsystems are the highest-risk code in the project and need precise repro steps.
