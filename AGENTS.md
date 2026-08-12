# Muzilla agent guide

This file contains durable repository guidance. Keep it concise and current; do not create
parallel phase plans, recovery histories, or copied prompts.

## Sources of truth

Read these before changing product behavior or architecture, in order:

1. `docs/product-spec.md` — normative product behavior and interaction model.
2. `docs/completion-matrix.md` — only known unfinished work and unresolved decisions.
3. `docs/production-readiness.md` — durable completion and release-candidate gates.
4. Current code and tests — evidence of what is actually implemented.

When code and the product specification differ, do not hide the discrepancy: implement the
relevant completion item or update the specification only when the intended contract itself
has deliberately changed.

## Allowed models

Allowed:

- `openai/gpt-5.6-sol` — `high`
- `openai/gpt-5.6-luna` — `max`
- `opencode-go/deepseek-v4-flash` — `max`

Forbidden:

- every other model, variant, effort, or fallback.

Role tendencies do not bypass tests or review:

- Sol High: orchestration, difficult reasoning, architecture/product decisions, critical
  boundaries, and final adjudication/review.
- Luna Max: implementation with a sufficiently clear contract, refactoring, frontend/backend
  work, tests, cleanup, and deterministic multi-file execution.
- DeepSeek V4 Flash Max: repository exploration, broad searches, isolated implementation with
  clear contracts, test generation, high-volume analysis, and independent checks.

## Product and safety invariants

- Muzilla manages metadata; it is not an audio player or listening-library manager.
- The library may be one flat folder containing albums and loose singles with poor tags.
- Files are the source of truth; the database is an index and must detect external drift.
- No scan, provider fetch, candidate selection, edit, or review decision writes music.
- Every file mutation goes through the reviewed apply/recovery path with containment,
  preconditions, journal/recovery, and an explicit per-file result.
- ReviewBundle is the primary user-facing review. Technical jobs remain separate; never claim
  global filesystem atomicity.
- Grouping is internal inference. Do not restore arbitrary cross-album reassignment or expose
  implementation terminology as a primary user model.
- Current, proposed, and attempted state remain distinct.
- Preserve auth, CSRF/origin checks, security headers, proxy/path/symlink defenses, non-root
  container operation, reset safety, and secret redaction unless an equal or stronger
  replacement is verified.

## Architecture boundaries

Import-linter contracts are authoritative. Intended direction:

```text
domain          → nothing
tags, paths     → domain
providers       → domain
matching        → domain, providers
db              → domain
changes         → domain, db, tags, paths
pipeline        → lower layers
jobs            → lower layers, pipeline
services        → lower layers, jobs
api, cli        → services only
```

Additional rules:

- `domain/fields.py` is the canonical field registry.
- API and CLI do not import database models directly.
- Mutagen and SQLAlchemy remain synchronous; async belongs at HTTP/worker boundaries.
- Large sorted collections use cursor/keyset pagination, not OFFSET.
- Provider search summaries and hydrated candidate details are different contracts.
- Frontend server types come from generated OpenAPI types; view adapters must not recreate the
  API schema.

## Working method

- Work from one or more explicit IDs in `docs/completion-matrix.md`; keep the scope coherent
  and update/remove only items genuinely completed by the work.
- Before coding, reproduce the issue or add a failing test at the boundary where the behavior
  is wrong. Never weaken or rewrite a test merely to make current behavior green.
- A user-observed application reproduction is primary evidence for visible behavior. Preserve
  it as automated acceptance or a precise manual check.
- State expected behavior and failure semantics before changing a critical boundary.
- Prefer small migrations and adapters, but do not leave compatibility layers without a
  completion item and an exit condition.
- Resolve ordinary implementation questions from the specification, code, and tests. Keep a
  genuine product/UX choice as a decision item until its consequence is implemented and
  verified.
- Never close an item merely by editing documentation. A decision item is not complete when a
  label is chosen; implement and test the chosen outcome first.
- Preserve unrelated user changes in a dirty worktree.
- Do not inspect or use real music, `data/`, `music/`, secrets, backups, or `.env*` as fixtures.
- Do not create tags, releases, deployments, pushes, or other external writes unless explicitly
  authorized. Commits are allowed only when explicitly authorized by the user or the active
  workflow policy.

## Verification

Run the narrowest relevant checks while iterating, then the applicable parts of
`docs/production-readiness.md` before handoff.

Backend:

```bash
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

Frontend:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

Run `cd e2e && npm run test` when a user journey, router, browser state, or file flow changes.
Docker/native/deployment work must be verified in the exact built image and isolated Compose
resources; HTTP health alone does not prove native capability.

Provider tests use deterministic contract fixtures, not live services. FTS5 tests use migrated
database fixtures. Tests that need build output create deterministic fixtures; ignored local
artifacts are not test inputs.

## Handoff

Report changed files, commands/results, product behavior, migration impact, residual risk, and
the affected completion IDs. Remove an item only after its acceptance criteria and relevant
readiness checks pass. Leave no scratch plan, temporary report, or copied prompt in `docs/`.
