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
- Preserving unrelated user changes means leaving their worktree and index state
  exactly as found. Never use `git restore`, `git checkout`, `git reset`, `git clean`,
  or equivalent commands to remove pre-existing user changes merely to obtain a
  goal-scoped diff or clean working tree. Exclude unrelated paths from inspection,
  staging, commits, and finalization instead.
- In particular, pre-existing changes under `.pi/` are user-owned runtime/orchestration
  configuration. Never restore or normalize them as goal cleanup unless the user
  explicitly requests that exact change.
- Repository-local `music/` is an intentionally disposable test sandbox. Agents may inspect,
  create, modify, rename, move, corrupt, or delete files inside this directory as needed for
  implementation, tests, E2E, apply/undo, recovery, migration, and destructive safety testing.
  No content inside repository-local `music/` needs to be preserved unless a specific test
  requires it.
- This permission applies only to the repository-controlled `music/` sandbox. Never inspect,
  modify, import, reset, or delete user-owned music or arbitrary music paths outside that
  sandbox.
- Do not use real `data/`, secrets, backups, `.env*`, or other user-owned state as fixtures.
- Local checkpoint commits are allowed during autonomous goal execution when they improve
  recoverability, reviewability, or continuation across sessions. Only the active `worker`
  writer lane may create implementation checkpoint commits; the parent does not create them.
  If the user explicitly requests `no commit`, the worker MUST NOT create checkpoint commits.
- Checkpoint commits are local-only execution checkpoints and MUST NOT be pushed during an
  in-progress goal. The parent performs the single final normal push only after the exact final
  candidate has passed all required acceptance, review, browser, and readiness gates.
- Keep commits scoped and internally coherent; do not commit known-broken intermediate states
  merely to create progress checkpoints.
- A successfully completed autonomous goal must end with a final local commit containing the
  completed goal state unless the user explicitly requests `no commit`.
- Successful autonomous goals are explicitly authorized to push their completed local commits
  to the current branch's already-configured upstream as the final delivery step unless the
  user explicitly requests `no push`. A `no commit` request also suppresses the goal's final
  push because there is no goal commit to deliver.
- This standing push authorization applies only to a normal fast-forward `git push` of the
  current branch after all required acceptance, review, browser, and readiness gates have
  passed. It does not authorize force-push, changing remotes, creating or switching branches,
  tags, releases, deployments, or other external writes.
- When a final push is required, if the current branch has no configured upstream, or if the
  push is rejected or would require reconciliation with remote history, do not choose a remote,
  pull, merge, rebase, reset, force-push, or otherwise rewrite history automatically. Preserve
  the completed local commit, report the push blocker, and do not call `goal_complete` until the
  required final push can be completed safely.
- Do not rewrite, squash, amend, rebase, reset, or otherwise alter existing user-authored
  history unless explicitly authorized.

## Pi orchestration

The parent session is the orchestrator.
`pi-subagents` is parent-only and must never appear in any child `skill` or `skills` field.

### Orchestration economy

Agent delegation is a scarce resource. Prefer direct parent inspection over
delegation unless a role is explicitly required below.

For one completion-matrix ID, the default orchestration is:

1. Scouts only when the parent cannot establish the relevant
   implementation boundary efficiently by direct read-only inspection.
2. One retained worker run for the entire goal. Resume that same worker for
   implementation fixes, reviewer findings, browser findings, matrix updates,
   and other goal-owned repository edits. Do not spawn a replacement worker
   unless the retained worker cannot continue.
3. One fresh reviewer after the coherent implementation candidate exists.
4. At most one additional fresh reviewer after material fixes.
5. Browser-tester only when the acceptance contract contains browser-visible
   behavior that cannot be established by non-browser evidence.
6. Oracle only when an actual unresolved decision exists whose wrong resolution
   could materially affect architecture, safety, concurrency, migration, or
   recovery. Merely touching those areas is not sufficient.
7. Do not spawn agents merely to confirm evidence already established by the
   repository, tests, or another required specialist.

### Project override: single repository-content writer

This project intentionally overrides any generic orchestration guidance that suggests applying
review fixes directly in the parent.

For completion-matrix work:

- `worker` is the **only agent that edits repository content** in the active worktree.
- Repository content includes application code, tests, migrations, generated artifacts,
  normative documentation, `docs/completion-matrix.md`, and any other tracked or untracked
  project file created or changed for the goal.
- The parent MUST NOT use `edit`, `write`, or shell commands that modify repository file content.
- The parent may use read-only inspection and verification commands and, after the exact
  candidate is accepted, Git finalization commands required to stage, commit, push, and verify
  the already-written candidate. The parent must not intentionally use those commands to
  rewrite repository file contents or history. If a commit hook or other commit-time action
  unexpectedly modifies tracked content, that mutation invalidates the accepted candidate and
  becomes worker-owned repository content; the parent does not repair it directly.
- The active `worker` may create local checkpoint commits for its own coherent in-progress work
  when they materially improve recoverability, reviewability, or continuation. A checkpoint
  commit never authorizes an intermediate push and never changes the parent's final acceptance
  authority.
- Initial implementation MUST be delegated to `worker`.
- Every accepted reviewer or browser-tester finding that requires a repository change MUST be
  delegated back to `worker`.
- Completion-matrix and goal-owned documentation updates MUST be delegated to `worker`; the
  parent decides when they are justified but does not edit them itself.
- A reviewer `BLOCK` is a handoff to `worker`, not permission for the parent or reviewer to fix
  repository content directly.
- If `worker` cannot be launched, stop repository mutation and report a blocker. Never silently
  fall back to parent-authored edits.
- Prefer one worker for one coherent change. Do not create multiple mutation-capable lanes in
  the same worktree.

### Agent roles and capability boundaries

The runtime tool allowlists in `.pi/settings.json` are authoritative capability ceilings.
Prompts must not ask an agent to perform work its configured tools cannot perform.

| Agent | Purpose | Context | Allowed capability | Must not do |
| --- | --- | --- | --- | --- |
| `scout` | Local repository reconnaissance, dependency tracing, test/contract discovery | fork | read/search + supervisor contact | shell execution, writes, implementation, review fixes |
| `researcher` | External/current documentation when repository evidence is insufficient | fresh | read + web research + supervisor contact | repository writes, implementation, broad research without a concrete gap |
| `worker` | Initial implementation, all repository edits, accepted fixes, and optional local checkpoint commits | fork | read/search, shell validation, edit/write, local Git checkpoint, supervisor contact | make unapproved product/architecture decisions, push, spawn subagents |
| `reviewer` | Independent requirements/code review | fresh | read/search + supervisor contact | shell execution, writes, implementation, applying fixes |
| `oracle` | Decision-consistency check for S1/architecture/safety/concurrency/migration/recovery | fork | read/search, read-only shell inspection, supervisor contact | writes, implementation |
| `delegate` | Lightweight read-only analysis when no specialist role fits | fork | read/search + supervisor contact | implementation, writes, replacing worker/reviewer/oracle |
| `browser-tester` | Independent browser-visible acceptance | fresh | read/search, browser MCP, limited shell/runtime control, supervisor contact | edit/write application code |

The oracle follows the upstream `pi-subagents` role contract: forked context is intentional so
it can reconstruct inherited decisions and detect drift; its `bash` access is for inspection,
verification, and read-only analysis only.

Do not broaden a child's tools merely to avoid a handoff. If a role lacks a required capability,
return the work to the parent and delegate it to the appropriate role.

### Skill routing

Use skills through Pi's native harness rather than copying their instructions into prompts.
Keep skill selection narrow: `inheritSkills: false` prevents normal children from receiving the
whole discovered catalog, while an explicit skill can still be attached to the run that needs
it.

- Parent/orchestrator: use `pi-subagents` for delegation/orchestration. Use `council-mode` only
  when multiple independent model perspectives are genuinely needed for a decision; do not use
  it as routine review fanout.
- Worker: no inherited skills by default. For material UI/UX implementation, launch the worker
  with the project `ui-ux-pro-max` skill explicitly attached to that run.
- Reviewer: no inherited skills by default. For material UI/UX review, launch the reviewer with
  `ui-ux-pro-max` explicitly attached to that run.
- Browser tester: use the explicit project `ui-ux-pro-max` skill defined by its project agent;
  do not inherit unrelated parent skills.
- Scout/researcher/oracle/delegate: attach a specialist skill only when the delegated task
  specifically requires it and the role remains within its capability ceiling.

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
  migration/recovery decision. Use its forked context to challenge inherited decisions and
  detect drift; do not repeatedly call it for ordinary implementation details.
- `worker` for implementation and every repository-content edit, including accepted fixes and
  completion-matrix/documentation updates. The same active worker lane may create a local
  checkpoint commit when useful, but it never pushes.
- `reviewer` after a coherent candidate exists. Re-review only after material fixes; ask the
  next fresh review to verify the prior blockers plus the acceptance contract rather than
  restarting broad reconnaissance without reason.
- `browser-tester` only when browser-visible acceptance is materially affected.
- `delegate` only for bounded read-only support that does not fit another specialist.

Do not create recursive subagent fan-out. Ordinary child agents are not orchestrators.

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

The reviewer is read-only in this project. It reports concrete findings with evidence and the
smallest recommended fix; it does not run tests or edit files. Any required repository change is
assigned to `worker`.

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
2. Attach `ui-ux-pro-max` explicitly to the implementation `worker`.
3. Use its search/design workflow to identify applicable UX patterns, accessibility guidance,
   anti-patterns, and stack-specific recommendations.
4. Adapt those recommendations to Muzilla's existing design language rather than redesigning
   unrelated surfaces.
5. Attach `ui-ux-pro-max` explicitly to the independent fresh-context `reviewer`.
6. Exercise browser-visible acceptance with `browser-tester`.
7. Return accepted reviewer/browser findings to `worker`.

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
frontend, migration, recovery, or other required gates. The browser tester does not edit
repository content; accepted failures return to `worker`.

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

Explicit user delivery overrides affect only the final commit/push delivery steps; they do not
weaken acceptance, review, browser, or readiness requirements:

- `no commit`: do not create worker checkpoint commits or a final goal commit. This also implies
  `no push` for the goal because there is no goal commit to deliver.
- `no push`: create or identify the final local commit normally unless `no commit` also applies,
  but do not push it.
- A goal using either override may still reach `goal_complete` once every applicable non-delivery
  requirement has passed and the requested delivery exception is recorded explicitly in the
  completion evidence.

### Goal preflight

Before any repository-content write for a requested completion ID:

1. Locate the exact row in `docs/completion-matrix.md`.
2. Read its current state, expected final behavior and acceptance, relevant areas,
   dependencies, and risk.
3. Read the relevant normative product-spec sections.
4. Inspect whether every listed dependency is already resolved.
5. Inspect the relevant implementation and existing tests.
6. Establish a concrete reproduction or failing acceptance boundary where applicable.
7. Use `scout` for focused reconnaissance when the area is non-trivial.
8. If external current evidence is materially required, use `researcher` for that specific gap.
9. For Risk S1 or a material architecture/safety/concurrency/migration/recovery decision,
   consult the forked-context `oracle` before the first repository write.

If the requested ID does not exist, is already absent/completed, or still has an actionable
dependency, do not invent replacement work.

An open matrix dependency is a scope blocker for that requested ID; it is not permission to
implement the dependency unless the user explicitly requested it.

### Goal execution

For a ready ID:

1. Parent completes preflight and required agent consultation.
2. Delegate initial implementation to `worker`.
3. `worker` performs focused checks during iteration and returns changed files and evidence. It
   may create a coherent local checkpoint commit when that materially improves recoverability,
   reviewability, or continuation, unless `no commit` applies; it MUST NOT push a checkpoint.
4. Run a fresh-context `reviewer` against the acceptance contract.
5. If browser-visible behavior changed, run `browser-tester`.
6. Parent synthesizes reviewer/browser findings and decides which findings are in scope.
7. Return every accepted finding that requires a repository change to `worker`.
8. Repeat focused checks and fresh review/browser acceptance after material worker fixes where
   applicable; do not create extra review rounds merely for activity.
9. Parent performs the acceptance audit.
10. Run the applicable readiness gates required to justify completion of the matrix row on the
    implementation candidate before removing or changing that row to represent completion.
11. Once acceptance evidence, independent review, and those readiness gates justify completion,
    delegate the completion-matrix update and any other goal-owned repository
    documentation/generated-file edit to `worker`.
12. Run every applicable readiness gate on the exact final candidate, including the worker-owned
    completion-matrix update and every other tracked goal-owned change that will be delivered.
    This final pass is intentional even when the same gate passed before the matrix update.
13. If a final gate or review exposes a required repository change, return it to `worker` and
    rerun only the affected review/gates plus any mandatory final gate set.
14. After the exact candidate is accepted and no repository-content edit remains, apply the
    requested delivery mode. Unless `no commit` applies, the parent may stage and create the
    final local commit. If the exact accepted candidate is already represented by `HEAD` because
    the worker's last checkpoint commit contains it, do not create an empty commit; record that
    `HEAD` as the final commit instead. Do not include unrelated pre-existing user changes. If
    `no commit` applies, leave the accepted goal-owned changes uncommitted and record that state.
15. When a final commit exists, record its SHA. If commit hooks or the commit process unexpectedly
    modify tracked content, the accepted candidate is invalidated. The parent MUST NOT repair
    those files directly: delegate the resulting repository-content change to `worker`, rerun
    affected gates, and create or identify the corrected final commit when commit delivery is
    required.
16. Unless `no push` or `no commit` applies, push the current branch to its already-configured
    upstream using a normal `git push`. Never use `--force`, `--force-with-lease`, or another
    history-rewriting push mode.
17. When a final push is required, verify that the configured upstream resolves to the same
    commit as local `HEAD`.
18. Call `goal_complete` only after all required evidence is green and the applicable delivery
    contract is satisfied: the final goal state is committed unless `no commit` applies, and
    the final commit is pushed and matches the configured upstream unless `no push` or
    `no commit` applies.

Do not call `goal_complete` while completed goal-owned changes violate the selected delivery
mode. In normal delivery, goal-owned changes must be committed and the final commit must have
been successfully pushed to the configured upstream. With `no push`, the accepted final local
commit must remain unpushed. With `no commit`, the accepted goal-owned changes must remain
uncommitted and unpushed. A failed or rejected push is an incomplete delivery only when a final
push is required.

### Goal completion evidence

`goal_complete` requires the exact current `goal_id` and a summary containing:

- requested completion-matrix ID;
- any additional IDs explicitly included by the user;
- acceptance-criterion-by-acceptance-criterion evidence;
- changed files;
- delivery mode: normal, `no push`, or `no commit`;
- final local commit SHA when a final commit is required or already represents the accepted
  candidate; otherwise the current `HEAD` plus explicit confirmation that the goal-owned changes
  remain uncommitted by user request;
- pushed branch and configured upstream, final push result, and verification that local `HEAD`
  and the configured upstream resolve to the same commit when a push is required; otherwise
  explicit confirmation that push was skipped by user request;
- relevant commands and exact pass/fail results;
- independent reviewer result;
- browser acceptance result when applicable;
- generated-contract impact when applicable;
- migration/database impact when applicable;
- exact-image/runtime impact when applicable;
- residual risk.

All required gates must apply to the exact goal-owned content being delivered. In normal or
`no push` delivery, that content is the final commit candidate. With `no commit`, it is the exact
accepted uncommitted goal-owned worktree content. Creating the final commit must not materially
change the tested candidate. If commit hooks or other commit-time actions change tracked
content, the candidate is invalidated and the affected gates must be rerun before delivery.

When a final push is required, `goal_complete` requires a successful final push and verification
that the current branch's configured upstream resolves to the final local commit SHA. When
`no push` or `no commit` applies, that push requirement is intentionally waived and the skipped
delivery action must be recorded explicitly. Do not create an empty commit when the completed
candidate is already exactly represented by `HEAD`.

### Goal blocked state

Use `goal_blocked` only for a genuine blocker that prevents safe progress and can be supported
with concrete evidence.

Do not manufacture work, weaken acceptance criteria, bypass a dependency, contact live
providers, alter user-owned data, or silently broaden scope merely to avoid a blocked state.

If provider/session limits interrupt work, preserve truthful repository and matrix state.
A later session must resume from current repository evidence rather than assuming the previous
session completed unfinished work.

## Readiness gates for autonomous completion

`goal_complete` may be called only when all applicable gates below pass on the exact final
candidate content. In normal delivery that candidate is subsequently recorded in the final local
commit and successfully pushed to the current branch's configured upstream. `no push` and
`no commit` change only those delivery requirements; they do not waive or weaken readiness gates.

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

The completion matrix contains unfinished work only. The parent decides whether acceptance
evidence justifies removing or changing a row, but `worker` performs the repository edit.

Remove a row only when:

1. its implementation exists;
2. every acceptance requirement has evidence;
3. relevant regression/acceptance tests exist at the correct boundary;
4. applicable readiness gates pass on the implementation candidate before row removal, followed
   by the required exact-final-candidate gate pass after the worker-owned matrix update;
5. independent review has no blocking finding.

Do not:

- remove a row because documentation now describes the desired behavior;
- mark a dependency resolved because dependent work happens to pass;
- close nearby IDs because their code was touched;
- retain a completed row merely as historical documentation.

Git history and task handoff are the historical record.

When an implementation reveals new unfinished work that is genuinely outside the requested
row's acceptance contract, preserve the current row accurately and add a new completion item
only when the new work is concrete, actionable, non-duplicative, and necessary. Any such
repository edit is performed by `worker` after the parent approves its scope.

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
