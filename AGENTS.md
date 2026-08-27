# Muzilla agent guide

This file is the durable repository contract for human and agent work. Keep it concise and
current. Do not create parallel phase plans, recovery histories, copied task prompts, or
implementation diaries in the repository; Git history is the historical archive.

## Sources of truth

Read these before changing product behavior or architecture, in order:

1. `docs/product-spec.md` — normative product behavior and interaction model.
2. `docs/completion-matrix.md` — only known unfinished implementation, test, and acceptance work.
3. `docs/production-readiness.md` — durable completion and release-candidate gates.
4. `README.md` — operator-facing product overview and deployment.
5. Current code and tests — evidence of what is actually implemented.

When code and the product specification differ, do not hide the discrepancy. Implement the
requested completion item, or change the specification only when the intended contract itself
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
- There is no auto-apply in any mode or interface. A strong match only preselects a candidate
  that is applied through the user's explicit Apply. Candidates are strong, ambiguous
  (explicit choice or Skip; unresolved blocks the whole bundle), or rejected (hidden,
  unselectable, unforceable).
- The web UI is the primary interface and the only surface that applies metadata/file changes.
  The CLI is support and troubleshooting tooling and never applies or auto-applies in any mode.
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
(`pyproject.toml` `[tool.importlinter]`); listed edges are permitted subsets and unused edges
are not added.

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
- Before implementation, inspect the requested row's `Dependencies`.
- An ID is ready only when every listed dependency is no longer an actionable row in
  `docs/completion-matrix.md`.
- If a dependency is still actionable, do not silently absorb it into the requested ID and do
  not begin dependent implementation. Report the unresolved dependency chain and stop cleanly.
- When multiple IDs are explicitly requested, verify dependency order and preserve separate
  acceptance evidence for each ID.
- Keep scope coherent and update/remove only items genuinely completed by the work.
- Before implementation, reproduce the issue or add a failing test at the boundary where the
  behavior is wrong when applicable. Never weaken a test merely to make current behavior green.
- A user-observed application reproduction is primary evidence for visible behavior. Preserve
  it as automated acceptance or a precise manual check.
- State expected behavior and failure semantics before changing a critical boundary.
- Prefer small migrations and adapters, but do not leave compatibility layers without a
  completion item and exit condition.
- Resolve ordinary implementation questions from the specification, code, and tests. Escalate
  genuine product/UX/architecture choices instead of silently inventing policy.
- Never close an implementation item merely by editing documentation.
- Preserve unrelated user changes in a dirty worktree.
- Repository-local `music/` is an intentionally disposable test sandbox. Agents may inspect,
  create, modify, rename, move, corrupt, or delete files inside it when required by tests.
- That permission applies only to repository-controlled `music/`. Never inspect, modify,
  import, reset, or delete user-owned music or arbitrary music paths outside that sandbox.
- Do not use real `data/`, secrets, backups, `.env*`, or other user-owned state as fixtures.
- Do not rewrite, squash, amend, rebase, reset, force-push, or otherwise alter user-authored
  history unless explicitly authorized.

## Pi orchestration contract

For non-trivial completion-matrix work, the **parent Pi session is the orchestrator and final
decision-maker**. In this project the configured parent is expected to be Terra. The parent
owns scope, delegation, synthesis, acceptance decisions, final verification, commit, push, and
`goal_complete`; it is not an implementation writer.

Use the installed `pi-subagents` skill as the parent-only orchestration guide. Child agents must
not receive or run `pi-subagents` themselves. Prefer the package's supported subagent workflow
mechanisms and fresh/forked contexts rather than ad-hoc recursive delegation.

### Project override: single-writer rule

This project intentionally overrides any generic orchestration guidance that suggests applying
review fixes directly in the parent.

For completion-matrix implementation work:

- `worker` is the **only application-code writer**.
- The parent MUST NOT use `edit`, `write`, or mutating shell commands to implement application
  code, tests, migrations, generated contracts, or reviewer/browser fixes.
- Initial implementation MUST be delegated to `worker`.
- Every accepted reviewer or browser-tester finding that requires a repository change MUST be
  delegated back to `worker`.
- A reviewer `BLOCK` is a handoff to `worker`, not permission for the parent or reviewer to fix
  code directly.
- The parent may inspect code, synthesize evidence, decide scope, run final verification, update
  orchestration state, and perform the final commit/push after the candidate is accepted.
- If `worker` cannot be launched, stop implementation and report a blocker. Never silently
  fall back to parent-authored implementation.
- Prefer one worker for one coherent change. Do not create multiple mutation-capable lanes in
  the same worktree.

This rule is deliberately stricter than the generic `pi-subagents` default. It exists to keep
model routing predictable and prevent expensive parent/reviewer models from becoming accidental
writers.

### Agent roles and capability boundaries

The runtime tool allowlists in `.pi/settings.json` are authoritative capability ceilings.
Prompts must not ask an agent to perform work its configured tools cannot perform.

| Agent | Purpose | Context | Allowed capability | Must not do |
|---|---|---|---|---|
| `scout` | Local repository reconnaissance, dependency tracing, test/contract discovery | fork | read/search + supervisor contact | shell execution, writes, implementation, review fixes |
| `researcher` | External/current documentation when repository evidence is insufficient | fresh | read + web research + supervisor contact | repository writes, implementation, broad research without a concrete gap |
| `worker` | Initial implementation and every accepted fix | fork | read/search, shell validation, edit/write, supervisor contact | make unapproved product/architecture decisions, spawn subagents |
| `reviewer` | Independent requirements/code review | fresh | read/search + supervisor contact | shell execution, writes, implementation, applying fixes |
| `oracle` | S1/architecture/safety/concurrency/migration/recovery decision check | fresh | read/search + supervisor contact | shell execution, writes, implementation |
| `delegate` | Lightweight read-only analysis when no specialist role fits | fork | read/search + supervisor contact | implementation, writes, replacing worker/reviewer/oracle |
| `browser-tester` | Independent browser-visible acceptance | fresh | read/search, browser MCP, limited shell/runtime control, supervisor contact | edit/write application code |

Do not broaden a child's tools merely to avoid a handoff. If a role lacks a required capability,
return the work to the parent and delegate it to the appropriate role.

### Skill routing

Use skills narrowly; do not inherit the whole parent skill catalog into normal child agents.
More skills increase prompt/context size and can blur role boundaries.

- Parent/orchestrator: use `pi-subagents` for delegation/orchestration. Use `council-mode` only
  when multiple independent model perspectives are genuinely needed for a decision; do not use
  it as routine review fanout.
- Worker: no inherited skills by default. For material UI/UX implementation, launch the worker
  with the project `ui-ux-pro-max` skill explicitly attached to that run.
- Reviewer: no inherited skills by default. For material UI/UX review, launch the reviewer with
  `ui-ux-pro-max` explicitly attached to that run.
- Browser tester: use the explicit project `ui-ux-pro-max` skill defined by its project agent;
  do not inherit unrelated parent skills.
- Scout/researcher/oracle/delegate: do not attach UI/design or orchestration skills unless the
  delegated task specifically requires one and the role remains within its capability ceiling.

Do not use `/parallel-review ... autofix` or another workflow that applies synthesized fixes in
the parent for completion-matrix work. A review workflow may collect findings, but repository
fixes always go through `worker`.

### Delegation policy

Use the minimum number of agents needed to close the requested acceptance contract:

- `scout` once for focused reconnaissance before implementation when the area is non-trivial.
  Re-scout only if the codebase state or scope materially changes.
- `researcher` only when current external facts or upstream behavior are necessary and cannot be
  established from repository evidence.
- `oracle` before the first write for Risk S1 or a material architecture/safety/concurrency/
  migration/recovery decision. Do not repeatedly call oracle for ordinary implementation details.
- `worker` for implementation and all accepted fixes.
- `reviewer` after a coherent candidate exists. Re-review only after material fixes; ask the
  next review to verify the prior blockers plus the acceptance contract, not to restart broad
  reconnaissance without reason.
- `browser-tester` only when browser-visible acceptance is materially affected.
- `delegate` only for bounded read-only support that does not fit another specialist.

Do not create recursive subagent fan-out. Ordinary child agents are not orchestrators.

## Independent review contract

Completion-matrix review is requirements review, not merely diff review. The reviewer must use
fresh context and independently read:

1. the requested completion-matrix row;
2. its expected final behavior and acceptance criteria;
3. applicable product-spec contracts;
4. the resulting implementation and tests.

Reviewing only the diff is insufficient. Start from the exact changed seams and named acceptance
contract, then broaden only when exhaustive verification is required.

The reviewer must check:

- every acceptance requirement is implemented;
- negative/failure semantics match the contract;
- tests exercise the boundary where prior behavior was wrong;
- no unrelated completion item was implicitly closed;
- architecture and product invariants remain intact;
- migrations, generated contracts, persistence, restart, concurrency, security, or recovery
  consequences are covered when applicable;
- test changes do not weaken the intended contract.

The reviewer is read-only in this project. It reports concrete findings with evidence and the
smallest recommended fix; it does not run tests or edit files. The parent/worker supplies test
results as evidence, and any required code change is assigned to `worker`.

Material correctness, safety, acceptance, security, migration, or architecture findings block
completion. `OK with notes` is acceptable only when every remaining note is demonstrably
non-blocking for the requested ID.

## UI/UX design contract

For work that creates, changes, or materially reviews browser-visible UI/UX, use the
`ui-ux-pro-max` project skill before making design decisions.

This includes page/component layout, navigation, information architecture, forms, responsive
behavior, typography/spacing/density, accessibility, loading/empty/error/destructive states,
tables/lists/filters/facets, visualization, motion, and UX consistency.

The skill provides design intelligence and heuristics; it is not a normative product source.
When it conflicts with `docs/product-spec.md`, the completion-matrix acceptance contract,
Muzilla safety invariants, or accessibility requirements, the repository contract wins.

For material UI/UX work:

1. Read the requested row and relevant product contract.
2. Attach `ui-ux-pro-max` explicitly to the implementation worker.
3. Adapt its recommendations to Muzilla's existing design language.
4. Attach `ui-ux-pro-max` explicitly to the fresh reviewer.
5. Exercise browser-visible acceptance with `browser-tester`.
6. Route any accepted reviewer/browser finding back to `worker`.

Do not introduce a new design language, color system, typography system, component library, or
interaction paradigm merely because a generic skill recommends it.

## Browser acceptance contract

Use `browser-tester` when work changes a user journey, router behavior, browser state,
interaction boundary, responsive behavior, file-flow UI, or another browser-visible acceptance
condition.

The browser tester must exercise the actual application rather than infer success from source.
When applicable it reports:

- flow exercised;
- expected behavior;
- observed behavior;
- console/runtime errors;
- relevant failed network requests;
- reproducible failures;
- final PASS or FAIL.

A browser PASS is evidence for browser acceptance only. It does not replace backend, frontend,
migration, recovery, or other required gates. The browser tester never edits application code;
accepted failures return to `worker`.

## Verification

Run the narrowest relevant checks while iterating, then the applicable parts of
`docs/production-readiness.md` on the final candidate.

Backend baseline:

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

Run `cd e2e && npm run test` when a user journey, router, browser state, or file flow changes.

Docker/native/deployment work must be verified in the exact built image and isolated Compose
resources; HTTP health alone does not prove native capability. Provider tests use deterministic
contract fixtures, not live services. FTS5 tests use migrated database fixtures. Tests that need
build output create deterministic fixtures; ignored local artifacts are not test inputs.

When OpenAPI changes, regenerate frontend types using the repository command and require the
generated result to be clean and in sync. Database/schema work must include applicable migration
checks from `docs/production-readiness.md`, including `alembic check` or its repository
equivalent where required.

Do not repeatedly run full readiness suites after every small edit. The worker should use
focused checks while iterating; run the complete applicable gate set once the candidate is
coherent, and rerun only affected gates after subsequent material fixes.

## Acceptance evidence

Passing generic quality gates does not itself prove that a completion-matrix item is complete.
Before removing a matrix row or calling `goal_complete`, the parent performs an acceptance audit
mapping every material acceptance requirement to concrete evidence.

Evidence may include focused regression tests, integration tests, browser/E2E acceptance,
deterministic disposable-fixture evidence, migration/restart/recovery tests, exact-image checks,
generated-contract checks, command output, and direct structural repository evidence.

Every completed ID needs its own acceptance evidence. Do not replace specific evidence with
summaries such as "consistent", "looks green", or "all tests pass".

## Autonomous goal contract (`/goal`)

A normal autonomous request may be as short as:

```text
/goal Implementa <ID> della completion matrix.
```

The user prompt identifies the requested work. The execution contract comes from this file,
the requested matrix row, `docs/product-spec.md`, and `docs/production-readiness.md`.

### Goal preflight

Before any implementation write:

1. Locate the exact row in `docs/completion-matrix.md`.
2. Read current state, expected behavior/acceptance, relevant areas, dependencies, and risk.
3. Read relevant normative product-spec sections.
4. Confirm every listed dependency is resolved.
5. Inspect relevant implementation and existing tests.
6. Establish a concrete reproduction or failing acceptance boundary when applicable.
7. Use `scout` for focused reconnaissance when the area is non-trivial.
8. If external current evidence is materially required, use `researcher` for that specific gap.
9. For Risk S1 or a material architecture/safety decision, consult `oracle` before the first
   implementation write.

If the requested ID does not exist, is already absent/completed, or has an actionable
dependency, do not invent replacement work. An open dependency is a scope blocker, not
permission to implement that dependency unless the user explicitly requested it.

### Goal execution loop

For a ready ID, the parent follows this loop:

```text
parent preflight
  → scout (when non-trivial)
  → researcher (only if needed)
  → oracle (S1/material decision only)
  → worker implementation
  → focused worker checks
  → fresh reviewer
  → browser-tester (when applicable)
  → parent synthesizes findings
  → worker fixes accepted findings
  → focused re-checks
  → fresh re-review / browser re-check only after material fixes
  → acceptance audit
  → final applicable readiness gates
  → completion-matrix update
  → final review of the exact candidate if the matrix update materially changes review scope
  → commit
  → push
  → verify upstream == HEAD
  → goal_complete
```

Hard rules for this loop:

- The parent never implements or fixes application code.
- The reviewer never implements or fixes application code.
- Browser-tester never implements or fixes application code.
- `worker` performs the initial implementation and every repository fix.
- S1 requires oracle before the first implementation write, not after a reviewer discovers the
  architecture problem.
- Do not launch a new broad reviewer merely because the previous reviewer returned a finding.
  After fixes, give the fresh reviewer the prior findings, changed seams, and full acceptance
  contract; ask it to confirm regressions are closed and identify any remaining concrete issue.
- Stop review/fix cycling when review is clean, or when a genuine decision/provider/quota/blocker
  prevents safe progress. Do not manufacture additional review passes for activity.
- Do not use review-autofix modes that mutate from the parent.

### Goal finalization

A successfully completed autonomous goal must end with a final local commit containing the
completed goal state unless the user explicitly requests `no commit`.

After all required acceptance, review, browser, and readiness gates pass:

1. Update/remove only the requested completion row when its acceptance evidence exists.
2. Run any gate affected by that final matrix/generated/docs change.
3. Create a scoped final commit containing implementation, tests, generated artifacts,
   normative documentation changes, and the completion-matrix update. Do not include unrelated
   pre-existing user changes.
4. Record the final commit SHA. If commit hooks modify tracked content, rerun affected gates and
   create the corrected final commit before proceeding.
5. Push the current branch to its already-configured upstream with a normal `git push`.
6. Never use `--force`, `--force-with-lease`, choose a new remote, pull/rebase/merge/reset to
   repair a rejected push, or rewrite history automatically.
7. Verify the configured upstream resolves to the same commit as local `HEAD`.
8. Call `goal_complete` only after the exact final candidate is green, committed, pushed, and
   upstream equality is verified.

If the branch has no configured upstream, push is rejected, provider/session limits interrupt
work, or another genuine blocker prevents completion, preserve truthful repository state and use
`goal_blocked` only when concrete evidence supports it. A later session resumes from repository
evidence; it must not assume unfinished work completed.

### Goal completion evidence

`goal_complete` requires the exact current `goal_id` and a summary containing:

- requested completion-matrix ID and any additional explicitly requested IDs;
- acceptance-criterion-by-acceptance-criterion evidence;
- changed files;
- relevant commands and exact pass/fail results;
- independent reviewer result;
- browser acceptance result when applicable;
- migration/generated-contract/runtime impact when applicable;
- final local commit SHA;
- pushed branch and configured upstream;
- final push result;
- verification that local `HEAD` and upstream resolve to the same commit;
- residual risk.

## Completion-matrix updates

The completion matrix contains unfinished work only. Remove a row only when:

1. its implementation exists;
2. every acceptance requirement has evidence;
3. relevant regression/acceptance tests exist at the correct boundary;
4. applicable readiness gates pass;
5. independent review has no blocking finding.

Do not remove a row because documentation describes desired behavior, mark a dependency resolved
because dependent work happens to pass, close nearby IDs because their code was touched, or keep
a completed row merely as historical documentation.

When implementation reveals genuinely new unfinished work outside the requested row's acceptance
contract, preserve the current row accurately and add a new completion item only when the work is
concrete, actionable, non-duplicative, and necessary.

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
- final commit/push state for autonomous goals;
- residual risk or remaining blocker.
