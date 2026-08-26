# Muzilla agent guide

This file contains durable repository guidance. Keep it concise and current; do not create
parallel phase plans, recovery histories, copied task prompts, or temporary implementation
reports. Git history is the historical archive.

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

Hermes or other agent memory is supporting historical context only. It never overrides the
current repository, tests, completion matrix, product specification, production-readiness
contract, or this file.

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

- Work from one explicit ID in `docs/completion-matrix.md` unless the user explicitly requests
  a multi-ID work package.
- A completion-matrix ID is an implementation unit, not permission to opportunistically close
  nearby or related rows.
- Before coding, inspect the requested row's `Dependencies`.
- An ID is ready only when every listed dependency is no longer an actionable row in
  `docs/completion-matrix.md`.
- If a dependency is still actionable, do not silently absorb it into the requested ID and do
  not begin dependent implementation. Report the unresolved dependency chain and stop the
  requested work cleanly.
- When the user explicitly requests multiple IDs as one work package, verify that their
  dependency order is coherent, preserve the acceptance contract of every included ID, and
  report evidence separately for every completed ID.
- Keep scope coherent and update/remove only items genuinely completed by the work.
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
- Repository-local `music/` is an intentionally disposable test sandbox. Agents may inspect,
  create, modify, rename, move, corrupt, or delete files inside this directory as needed for
  implementation, tests, E2E, apply/undo, recovery, migration, and destructive safety testing.
  No content inside repository-local `music/` needs to be preserved unless a specific test
  requires it.
- This permission applies only to the repository-controlled `music/` sandbox. Never inspect,
  modify, import, reset, or delete user-owned music or arbitrary music paths outside that
  sandbox.
- Do not use real `data/`, secrets, backups, `.env*`, or other user-owned state as fixtures.
- Do not create tags, releases, deployments, pushes, or other external writes unless explicitly
  authorized.
- Local commits are allowed during autonomous goal execution and may be used as coherent
  checkpoints when they improve recoverability, reviewability, or continuation across sessions.
- Keep commits scoped and internally coherent; do not commit known-broken intermediate states
  merely to create progress checkpoints.
- A commit does not imply permission to push. Pushes, tags, releases, deployments, and other
  remote/external writes still require explicit user authorization.
- Do not rewrite, squash, amend, rebase, or otherwise alter existing user-authored history
  unless explicitly authorized.

## Agent orchestration

For non-trivial completion-matrix work, the parent agent owns orchestration and synthesis.

Use roles as follows:

- `scout` — repository reconnaissance, relevant-area discovery, dependency tracing, and
  identification of existing tests/contracts before implementation.
- `researcher` — current external documentation or upstream behavior when repository evidence
  is insufficient.
- `oracle` — architecture, safety, concurrency, migration, recovery, or other high-risk
  reasoning. Consult it before implementation for Risk S1 work or material architectural
  decisions.
- `worker` — implementation. The worker is the normal application-code writer. For material
  UI/UX work, it must use `ui-ux-pro-max`.
- `reviewer` — independent fresh-context verification. For material UI/UX work, it must also
  use `ui-ux-pro-max` to review usability, accessibility, interaction consistency, responsive
  behavior, and relevant anti-patterns.
- `browser-tester` — independent browser-visible acceptance verification using the configured
  browser tooling. It reviews; it does not modify application code.

Prefer a single writer for one coherent change. Reviewer and browser-tester findings return to
the worker for fixes.

Do not create recursive subagent fan-out unless explicitly required by the active workflow.

## Independent review contract

Completion-matrix review is requirements review, not merely code review.

Before reviewing an implementation, the reviewer must independently read:

1. the requested completion-matrix row;
2. its expected final behavior and acceptance criteria;
3. applicable product-spec contracts;
4. the resulting implementation and tests.

Reviewing only the diff is insufficient.

The reviewer must check:

- every acceptance requirement is implemented;
- negative/failure semantics match the contract;
- tests exercise the boundary where the prior behavior was wrong;
- no unrelated completion item was implicitly closed;
- architecture and product invariants remain intact;
- migrations, generated contracts, persistence, restart, concurrency, security, or recovery
  consequences are covered when applicable;
- test changes do not weaken the intended contract.

Material correctness, safety, acceptance, security, migration, or architecture findings block
completion.

An `OK with notes` result is acceptable only when every remaining note is demonstrably
non-blocking for the requested ID's acceptance contract.

## UI/UX design contract

For work that creates, changes, or materially reviews browser-visible UI/UX, use the
`ui-ux-pro-max` project skill before making design decisions.

This includes:

- page and component layout;
- navigation and information architecture;
- forms and interaction patterns;
- responsive/mobile behavior;
- typography, spacing, density, color, and visual hierarchy;
- accessibility and keyboard interaction;
- loading, empty, error, confirmation, and destructive states;
- tables, lists, filters, facets, dashboards, and data visualization;
- animation or motion;
- UX consistency reviews.

The skill provides design intelligence and heuristics; it is not a normative product source.
When its recommendations conflict with `docs/product-spec.md`, the completion-matrix acceptance
contract, existing Muzilla safety invariants, or accessibility requirements, the repository
contract wins.

For an implementation ID with material UI/UX scope:

1. Read the requested completion-matrix row and relevant product contract first.
2. Load `ui-ux-pro-max`.
3. Use its search/design workflow to identify applicable UX patterns, accessibility guidance,
   anti-patterns, and stack-specific recommendations.
4. Adapt those recommendations to Muzilla's existing design language rather than redesigning
   unrelated surfaces.
5. Have the independent reviewer evaluate both functional acceptance and UI/UX consistency.
6. Exercise browser-visible acceptance with `browser-tester`.

Do not introduce a new design language, color system, typography system, component library, or
interaction paradigm merely because the skill recommends a generic style. Prefer consistency
with Muzilla unless the requested completion ID explicitly requires a broader redesign.

## Browser acceptance contract

Use `browser-tester` when work changes a user journey, router behavior, browser state,
interaction boundary, responsive behavior, file-flow UI, or other browser-visible acceptance
condition.

The browser tester must exercise the relevant application behavior rather than infer success
from source code or unit tests alone.

When applicable, browser verification reports:

- flow exercised;
- expected behavior;
- observed behavior;
- console/runtime errors;
- relevant failed network requests;
- reproducible failures;
- final PASS or FAIL.

A browser PASS is evidence for browser acceptance only. It does not replace backend,
frontend, migration, recovery, or other required gates.

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

When OpenAPI changes, regenerate frontend types using the repository command and require the
generated result to be clean and in sync.

Database/schema work must include the applicable migration checks from
`docs/production-readiness.md`, including `alembic check` or its repository equivalent where
required.

## Acceptance evidence

Passing generic quality gates does not by itself prove that a completion-matrix item is
complete.

Before removing a matrix row or calling `goal_complete`, perform an acceptance audit that maps
every material acceptance requirement in the row to concrete evidence.

Evidence may include, as appropriate:

- focused regression tests;
- integration tests;
- browser/E2E acceptance;
- deterministic disposable-fixture evidence;
- migration/restart/recovery tests;
- exact-image checks;
- generated-contract checks;
- command output;
- direct repository evidence where the acceptance condition is structural.

Every completed ID must have its own acceptance evidence even when multiple IDs were explicitly
requested as one work package.

Do not replace specific acceptance evidence with summaries such as "consistent", "looks green",
or "all tests pass".

## Autonomous goal contract (pi-goal)

A normal autonomous request may be as short as:

```text
/goal Implementa <ID> della completion matrix.
```

The user prompt identifies the requested work. The execution contract comes from this file,
the requested matrix row, `docs/product-spec.md`, and `docs/production-readiness.md`; those
instructions do not need to be copied into each prompt.

### Goal preflight

Before modifying code for a requested completion ID:

1. Locate the exact row in `docs/completion-matrix.md`.
2. Read its current state, expected final behavior and acceptance, relevant areas,
   dependencies, and risk.
3. Read the relevant normative product-spec sections.
4. Inspect whether every listed dependency is already resolved.
5. Inspect the relevant implementation and existing tests.
6. Establish a concrete reproduction or failing acceptance boundary where applicable.

If the requested ID does not exist, is already absent/completed, or still has an actionable
dependency, do not invent replacement work.

An open matrix dependency is a scope blocker for that requested ID; it is not permission to
implement the dependency unless the user explicitly requested it.

### Goal execution

For a ready ID:

1. Use `scout` for focused reconnaissance.
2. For Risk S1 or material architecture/safety decisions, consult `oracle` before writing.
3. Delegate implementation to `worker`.
4. Run focused checks during iteration.
5. Run a fresh-context `reviewer` against the acceptance contract.
6. If browser-visible behavior changed, run `browser-tester`.
7. Return material reviewer/browser findings to `worker`.
8. Repeat review after material fixes.
9. Perform the acceptance audit.
10. Run every applicable readiness gate on the final candidate revision.
11. Update/remove only the requested completion row when its acceptance evidence exists.
12. Call `goal_complete` only after all required evidence is green on the same candidate
    revision that will be handed off.

Do not call `goal_complete` merely because implementation work stopped, the diff looks
reasonable, or generic tests pass.

### Goal completion evidence

`goal_complete` requires the exact current `goal_id` and a summary containing:

- requested completion-matrix ID;
- any additional IDs explicitly included by the user;
- acceptance-criterion-by-acceptance-criterion evidence;
- changed files;
- relevant commands and exact pass/fail results;
- independent reviewer result;
- browser acceptance result when applicable;
- generated-contract impact when applicable;
- migration/database impact when applicable;
- exact-image/runtime impact when applicable;
- residual risk.

All required gates must refer to the same current candidate/worktree revision that is being
handed off. Do not create a commit solely to satisfy this rule.

### Goal blocked state

Use `goal_blocked` only for a genuine blocker that prevents safe progress and can be supported
with concrete evidence.

Do not manufacture work, weaken acceptance criteria, bypass a dependency, contact live
providers, alter user-owned data, or silently broaden scope merely to avoid a blocked state.

If provider/session limits interrupt work, preserve truthful repository and matrix state.
A later session must resume from current repository evidence rather than assuming the previous
session completed unfinished work.

## Readiness gates for autonomous completion

`goal_complete` may be called only when all applicable gates below pass on the same current
candidate/worktree revision that will be handed off.

Backend always:

```bash
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

Frontend when `frontend/` is touched:

```bash
cd frontend
npm run lint
npm run typecheck
npm run test
npm run build
```

When OpenAPI changed, also run the repository's type-generation command and require a clean
generated diff.

E2E when a user journey, router, browser state, or file flow changed:

```bash
cd e2e
npm run test
```

Build the frontend first where required. Use deterministic fixtures and no live providers.

Any additional gate touched by the ID must also pass according to
`docs/production-readiness.md`, including as applicable:

- database/migration verification and `alembic check`;
- exact-image/runtime checks;
- `fpcalc` / `rsgain`;
- recovery/restart;
- backup/restore;
- reset safety;
- scale/performance;
- provider-secret handling;
- dependency/runtime audit.

Do not run heavyweight unrelated production-readiness gates merely to generate activity; run
the complete applicable set for the boundary actually changed.

## Completion-matrix updates

The completion matrix contains unfinished work only.

Remove a row only when:

1. its implementation exists;
2. every acceptance requirement has evidence;
3. relevant regression/acceptance tests exist at the correct boundary;
4. applicable readiness gates pass;
5. independent review has no blocking finding.

Do not:

- remove a row because documentation now describes the desired behavior;
- mark a dependency resolved because dependent work happens to pass;
- close nearby IDs because their code was touched;
- retain a completed row merely as historical documentation.

Git history and task handoff are the historical record.

When an implementation reveals new unfinished work that is genuinely outside the requested
row's acceptance contract, preserve the current row accurately and add a new completion item
only when the new work is concrete, actionable, non-duplicative, and necessary.

## Handoff

Report:

- affected completion ID or explicitly requested IDs;
- product behavior changed;
- changed files;
- acceptance evidence;
- commands and results;
- reviewer result;
- browser/E2E result when applicable;
- migration/generated-contract/runtime impact;
- residual risk or remaining blocker.

Leave no scratch plan, recovery diary, copied task prompt, or temporary report in `docs/`.

Do not create tags, releases, deployments, pushes, or other external writes unless explicitly
authorized.