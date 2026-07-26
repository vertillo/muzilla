# Contributing to muzilla

## Development setup

Backend:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
pre-commit install
```

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

See [`docs/PLAN.md`](docs/PLAN.md) for the full architecture. In short:

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
