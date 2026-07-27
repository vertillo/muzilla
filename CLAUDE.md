# muzilla — working conventions

Read this first when resuming work. The full architecture and phase
breakdown live in `docs/PLAN.md` (tracked in this repo — it is the
architectural contract, so it must be diffable and visible in a fresh
clone).

## What this project is

A self-hosted music **metadata** manager — like beets but scoped to
metadata only, with a real browser GUI. It never plays audio, never
manages a listening library, and never touches files until the user
reviews a diff and explicitly applies it.

**The defining constraint:** the target library is ONE FLAT FOLDER,
~50/50 albums and loose singles, with tag quality that varies wildly by
era. This invalidates beets' core heuristic ("the directory is the
album") — grouping must be inferred from tags and fingerprints, and
singletons are first-class, not a special case.

## Workflow

- **Commit every completed step**, not in one big batch at the end. Each
  commit should leave the tree green (lint + mypy + tests + layering).
- **Don't ask the user to resolve implementation-level questions** (test
  strategy, whether to pull a later plan step forward, which module a
  helper belongs in, etc.) — verify by reading/running the code and
  decide. Only ask when it's a genuine product/scope decision that
  isn't resolvable from the plan, the code, or the tests (e.g. "should
  apply/undo become async now" is a real product-scope call; "how
  should this specific test poll for a result" is not).
- **Conventional Commits**: `feat(scope):`, `fix(scope):`, `test(scope):`,
  `docs:`, `refactor(scope):`, `chore:`. Scopes match package names —
  `domain`, `tags`, `db`, `pipeline`, `services`, `api`, `cli`, `web`.
- Commit bodies explain **why**, especially where the design deviates
  from the obvious choice. Note deviations from beets/Picard explicitly.
- `docs/PROGRESS.md` is a **gotchas-and-decisions ledger**, not a status
  report. Add to it when you learn something a fresh session could not
  recover by reading the code or `git log` — a non-obvious fact about a
  dependency, or *why* a design went the way it did. Do not add phase
  status, test counts, commit lists or file inventories; those live in
  git and go stale immediately.
- `docs/PLAN.md` is the plan. Do not write a per-phase planning
  document — no plan-mode restatement of a phase that PLAN.md already
  specifies. If a section is ambiguous or silent, note the ambiguity,
  decide it from the code and tests, and record the decision in the
  commit body and in `docs/PROGRESS.md`. Never paraphrase the plan into
  a second document that implementation then follows instead.

## Never commit

- `.claude/settings.local.json` — per-machine harness overrides. The rest
  of `.claude/` IS tracked, so the permission rules and the `guard-rm`
  hook replicate across devices; anything added there must stay
  machine-independent (resolve paths from `$CLAUDE_PROJECT_DIR`/`$HOME`,
  never hardcode a checkout path) and must never contain credentials.
- Memory files under `~/.claude/projects/` — outside the repo entirely
- Anything under `data/`, `music/`, `secrets/`, `.env*`

## Verification before every commit

```bash
source .venv/bin/activate
ruff check src tests && mypy src && lint-imports && pytest -q
```

Frontend:
```bash
cd frontend && npm run lint && npm run typecheck && npm run build
```

## Architecture rules (enforced by import-linter in CI)

```
domain          → (nothing)
tags, paths     → domain
providers       → domain
matching        → domain, providers
db              → domain
changes         → domain, db, tags, paths
pipeline        → all above
jobs            → all above, including pipeline
services        → all above, including jobs
api, cli        → services ONLY
```

`api` and `cli` must never import `db.models` directly. This is what
makes CLI/API divergence architecturally impossible — both are thin
shells calling the same service functions.

Other invariants:

- **`domain/fields.py` is the canonical field registry.** Tag mapping,
  diff labels, path template variables, API schemas and UI forms all
  derive from it. Adding a metadata field means editing that one file.
- **Files are the source of truth; the DB is an index over them.** Users
  will edit tags with Picard behind muzilla's back, so drift must be
  *detected*, never steamrolled.
- **Nothing touches disk until a ChangeSet is applied** (Phase 2+).
- **Async only at the edges.** mutagen and SQLAlchemy are sync; the
  worker loop and HTTP client are async. Don't pretend otherwise.
- **Single-writer discipline for SQLite.** All writes route through one
  worker-owned session; API handlers read freely and enqueue writes.
- **Cursor pagination only** — never OFFSET over a large sorted table.

## Environment

- Python 3.12 via `uv` (system python is 3.9 — don't use it)
- `source .venv/bin/activate` before any backend command
- Node 22 for the frontend
- Docker Desktop must be running for container verification

## Testing conventions

- Audio fixtures are committed, pre-tagged, ffmpeg-generated 1s files in
  `tests/fixtures/audio/` (~270KB). CI needs no ffmpeg. Regenerate tags
  with `python tests/fixtures/audio/tag_fixtures.py`.
- Anything exercising FTS5 must use the `db_session` fixture (runs real
  Alembic migrations) — `Base.metadata.create_all()` doesn't create
  virtual tables or triggers.
- Provider tests (Phase 3+) must never hit the network by default.
