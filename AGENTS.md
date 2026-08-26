# Muzilla agent guide

This file contains durable repository guidance. Keep it concise and current; do not create
parallel phase plans, recovery histories, or copied prompts. Git history is the historical
archive.

## Sources of truth

Read these before changing product behavior or architecture, in order:

1. `docs/product-spec.md` — normative product behavior and interaction model.
2. `docs/completion-matrix.md` — only known unfinished implementation, test, and acceptance work.
3. `docs/production-readiness.md` — durable completion and release-candidate gates.
4. `README.md` — operator-facing product overview and deployment.
5. Current code and tests — evidence of what is actually implemented.

When code and the product specification differ, do not hide the discrepancy: implement the
relevant completion item or update the specification only when the intended contract itself
has deliberately changed.

## Product and safety invariants

- Muzilla manages metadata; it is not an audio player or listening-library manager.
- The library may be one flat folder containing albums and loose singles with poor tags.
- Files are the source of truth; the database is an index and must detect external drift.
- No scan, provider fetch, candidate selection, edit, or review decision writes music.
- Every file mutation goes through the reviewed apply/recovery path with containment,
  preconditions, journal/recovery, and an explicit per-file result.
- ReviewBundle is the only target review model and is atomic by default through its reviewed
  apply/rollback/recovery path. Partial success is not an accepted final outcome; a bundle
  converges only to fully applied, fully restored, or an explicit fail-closed recovery state.
  Technical jobs remain separate; do not claim a generic global filesystem transaction.
- There is no auto-apply in any mode or interface; a strong match only preselects a candidate
  that is applied through the user's explicit Apply. Candidates are decided as strong,
  ambiguous (explicit choice or Skip; unresolved blocks the whole bundle), or rejected
  (hidden, unselectable, unforceable).
- The web UI is the primary interface and the only surface that applies metadata/file
  changes. The CLI is support and troubleshooting tooling and never applies or auto-applies
  in any mode.
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
providers       → domain, db
matching        → domain, providers
audio           → (standalone tier; no muzilla imports)
db              → domain
changes         → domain, db, tags, paths
pipeline        → lower layers
jobs            → lower layers, pipeline
services        → lower layers, jobs
api, cli        → services only (plus config/logging support modules)
```

The diagram is a directional subset of the authoritative import-linter layers contract
(`pyproject.toml` `[tool.importlinter]`); listed edges are permitted subsets and unused
edges are not added.

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
  verified. Resolved decisions belong in normative documentation; preserve any remaining
  implementation/test work as a normal completion row.
- Never close an implementation item merely by editing documentation. Removing a resolved
  decision row is appropriate only after its decision is recorded normatively and its
  implementation work remains actionable elsewhere.
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

## Autonomous goal contract (pi-goal)

`goal_complete` may be called only when all gates below pass on the same revision that will be
handed off. A summary with only "consistent" or "looks green" is not evidence.

Gate (from Verification / production-readiness.md):
- Backend always: `uv run ruff check src tests && uv run mypy src && uv run lint-imports && uv run pytest -q --cov=muzilla --cov-report=term-missing`
- Frontend if `frontend/` touched: `cd frontend && npm run lint && npm run typecheck && npm run test && npm run build` plus `npm run generate-types` clean diff when OpenAPI changed
- E2E if user journey / router / browser state / file flow changed: `cd e2e && npm run test` (build frontend first, deterministic fixtures, no live providers)
- Any additional gate touched by the ID (DB migrations `alembic check`, exact-image `fpcalc`/`rsgain`, scale, backup/restore) must also pass per production-readiness.md

`goal_complete` requires: exact current `goal_id`, `summary` with ID, changed files, gate outputs (pass/fail preserved), and residual risk. `goal_blocked` only after same blocker 3 consecutive goal turns with evidence.

Persisted prompt template for every autonomous loop — copy verbatim replacing `{{ID}}`:

```text
/goal Risolvi esattamente 1 ID di docs/completion-matrix.md: {{ID}}.
Leggi in ordine: docs/product-spec.md, la riga {{ID}} in docs/completion-matrix.md (Current state / Expected final behavior / Relevant areas / Risk), docs/production-readiness.md, AGENTS.md.
Scope: solo {{ID}}. Non chiudere altri ID, non rimuovere righe per sola doc.
Metodo: scout (recon mirata su Relevant areas) -> se Risk S1 o decisione architetturale, chiedi oracle prima di scrivere -> worker (unico writer, edit minimi, un spelling per concetto) -> reviewer (verifica su diff, P0 blocca) -> se UX/E2E/browser, browser-tester con mcp:chrome-devtools. Se reviewer trova P0/P1, rientra da worker e ripeti fino a OK o OK with notes.
Verifica prima di completare: esegui i gate sopra pertinenti a {{ID}} e non indebolire test. Salva reproduction come test automatico dove la boundary è sbagliata. Mai auto-apply fuori web UI.
Completamento: chiama goal_complete solo con prove dei gate verdi sullo stesso commit; summary = ID + file cambiati + comandi/risultati + impatto migrazione + rischio residuo. Se bloccato esternamente 3 turn, usa goal_blocked con evidence.
Handoff: no scratch plan in docs/, report in risposta.
```

## Handoff

Report changed files, commands/results, product behavior, migration impact, residual risk, and
the affected completion IDs. Remove an item only after its acceptance criteria and relevant
readiness checks pass. Leave no scratch plan, temporary report, or copied prompt in `docs/`.
