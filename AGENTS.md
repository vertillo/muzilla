# Muzilla agent guide

This file is the durable repository guidance for coding agents. Keep it concise and
current. Task-specific implementation detail belongs in the recovery documents, not in
additional phase-plan files.

## Current source of truth

Read these before changing product behavior or architecture, in this order:

1. `docs/recovery-execution-guide.md` — slice workflow, model/effort guidance and prompts.
2. `docs/recovery-plan.md` — target architecture, migration order and acceptance gates.
3. `docs/ux-redesign.md` — target information architecture and interaction contracts.
4. `docs/issues-matrix.md` — issue status, dependencies and required tests.
5. `docs/recovery-audit.md` — evidence and confirmed root causes.

`docs/PLAN.md` and `docs/PROGRESS.md` describe the shipped pre-recovery implementation.
They remain useful historical references because code comments link to them, but they are
not normative for new product behavior. When they conflict with the recovery documents,
the recovery documents win. `docs/KNOWN_BUGS.md` is also a legacy index; track active
work in `docs/issues-matrix.md`.

## Product invariants

- Muzilla manages metadata; it is not an audio player or listening-library manager.
- The library may be one flat folder containing albums and loose singles with poor tags.
- Files are the source of truth; the database is an index and must detect external drift.
- No scan, provider fetch, candidate selection or review action writes music files.
- All file mutations go through the reviewed apply path, with containment, preconditions,
  journal/recovery and an explicit result.
- A unified review may aggregate several technical jobs; do not create one monolithic job
  or claim global filesystem atomicity.
- Keep grouping as an internal inference mechanism. Do not restore arbitrary cross-album
  reassignment or expose implementation terminology in the primary UX.
- Preserve current auth, security headers, path/symlink defenses, non-root container and
  secret redaction unless an equivalent or stronger replacement is verified.

## Working method

- Work on one vertical slice from `docs/recovery-plan.md` at a time.
- Before coding, reproduce the issue or add a failing test at the boundary where the bug
  actually exists. Do not change a test merely to make the current behavior green.
- State the expected behavior and failure semantics. For product-visible changes, check
  `docs/ux-redesign.md` before choosing labels, navigation or state.
- Prefer small migrations and adapters over a long-lived dual-write system.
- Do not add production dependencies or abstractions without a concrete need in the
  current slice.
- Preserve unrelated user changes in a dirty worktree.
- Do not edit or inspect real music, `data/`, `music/`, `secrets/` or `.env*` as fixtures.
- Do not create commits, tags, releases or external writes unless the task explicitly asks
  for them.
- Ask only for a genuinely blocking product choice or new authority. Resolve ordinary
  implementation questions from code, tests and the recovery documents.

## Architecture boundaries

The import-linter contracts are authoritative. The intended direction is:

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

Additional invariants:

- `domain/fields.py` is the canonical field registry.
- API and CLI must not import database models directly.
- Mutagen and SQLAlchemy remain synchronous; async belongs at HTTP/worker boundaries.
- Use cursor/keyset pagination for large sorted collections, not OFFSET.
- Provider search summaries and hydrated candidate details are different contracts.
- Proposed, current and attempted state must remain distinct.
- Frontend server types come from generated OpenAPI types; view-specific adapters may sit
  on top, but do not recreate the API schema manually.

## Verification

Run the narrowest relevant checks while iterating, then the slice gate before handoff.

Backend gate:

```bash
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q
```

Frontend gate:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

E2E when a user journey, router, browser state or file flow changes:

```bash
cd e2e
npm run test
```

Docker/native/deployment changes must also be verified inside the built image and, when
relevant, an isolated Compose project. A passing `/api/health` alone is not proof that
native capabilities such as `rsgain` work.

Provider tests must not hit live services by default. Use contract fixtures and explicit
failure cases. Anything using FTS5 must run against the migrated database fixture.

## Slice handoff

Before finishing a slice:

- update the relevant rows in `docs/issues-matrix.md` with status and actual tests;
- update recovery documents only when a decision or target contract changed;
- report changed files, commands/results, product behavior, migration impact and residual
  risk;
- leave no scratch plan, temporary report or copied prompt in `docs/`.
