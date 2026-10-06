# Muzilla agent guide

This file contains durable repository guidance. Keep it concise and current; do not create
parallel phase plans, recovery histories, copied task prompts, or temporary implementation
reports. Git history is the historical archive.

## Sources of truth

Read these before changing product behavior or architecture, in order:

1. `docs/product-spec.md` — normative product behavior and interaction model.
2. `docs/code-review-2026-10-04.md` — active remediation backlog, acceptance and
   dependency plan; its Docker appendix supplies supporting evidence.
3. `docs/completion-matrix.md` — exhausted original implementation backlog; do not repopulate
   it with the review findings.
4. `docs/production-readiness.md` — durable completion and release-candidate gates.
5. `README.md` — operator-facing product overview and deployment.
6. Current code and tests — evidence of what is actually implemented.

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

## Project workflow bindings

The orchestration rules below are portable: they operate on authorized acceptance obligations,
not on this project's names, IDs, or subsystem order. When transferring the setup, replace local
source/product/architecture/sandbox/verification/skill bindings and verify runtime capabilities;
do not transplant product-specific permissions or invariants. Keep the adaptive loop unchanged.
These bindings supply the local artifacts:

- Active backlog and closure ledger: `docs/code-review-2026-10-04.md`, including subsequently
  added findings. Preserve original diagnoses and historical evidence; update finding status
  and closure evidence, rather than copying findings into another backlog.
- Original backlog: `docs/completion-matrix.md`, currently exhausted. If used again, it contains
  unfinished work only; remove genuinely completed rows instead of retaining a closure history.
- Product and readiness contracts: `docs/product-spec.md` and `docs/production-readiness.md`.
- Hosted gate: `.github/workflows/ci.yml`; required jobs are `backend`, `frontend`, `e2e`,
  and `docker`. Commands and additional gates are defined under Verification and readiness below.
- Both explicit finding dependencies and phase prerequisites in the active plan apply.
  Priority labels P1/P2/P3 are not risk labels S1/S2/S3.

A full-backlog request explicitly authorizes multi-ID work under the project guides; their
single-ID examples remain valid narrow requests, not a required execution sequence. A narrow
request can complete with unrelated findings open. Full remediation covers every initially
open finding, including later additions already present at kickoff; production readiness also
requires the complete final gates on one candidate/image.

## Adaptive requirements workflow

Use one parent goal for the authorized outcome, whether it names one item, a package, or an
entire backlog. The acceptance obligation is the accounting unit; the smallest coherent change
is the implementation/review unit. Item IDs are not artificial code boundaries.

### Scope and current-state assessment

- A request to finish an entire backlog authorizes the parent to choose its next items and
  coherent packages without asking the owner to select another ID. Record the initially open
  obligations and delivery mode; do not expand the goal into an unbounded stream of new work.
- A narrow request does not authorize unrelated features or ledger closures. Fix a shared root
  cause across its affected callers when necessary, rather than duplicating item-specific fixes;
  record collateral satisfaction outside scope for later assessment, not automatic closure.
- Treat historical diagnoses and line references as investigation leads, and suggested
  interventions as proposals. Current normative acceptance, code, tests, and runtime evidence
  determine the necessary work. Do not mechanically execute historical interventions.
- Start with a lightweight backlog/dependency inventory, not exhaustive analysis or one scout
  per item. Before commissioning a fix, map each selected acceptance requirement to current
  evidence or a concrete residual gap at an identified revision.
- If behavior and adequate evidence already exist, commission no implementation. If only proof
  is missing, perform only the necessary verification or add the missing regression. If partly
  satisfied, repair only the residual. Different code or a green generic suite alone is not proof.
- Closure without new product code is valid when existing implementation and applicable evidence
  satisfy every requirement and independent review confirms it. Never weaken acceptance, remove
  supported behavior, or rewrite the product contract to make a historical finding disappear.
  A genuine contract/product ambiguity requires an owner decision.

### Selection and packaging

- Honor explicit and phase prerequisites; do not infer a total ordering from item numbering or
  phase numbering alone. Verify prerequisite closure/evidence before dependent implementation.
  Within a full-backlog goal, assess/close or implement an authorized prerequisite first, or
  include it in a dependency-coherent package. An out-of-scope prerequisite blocks that work,
  not permission to absorb it; continue independent authorized work when safe.
- Select by demonstrated residual need, material risk/priority, ability to stabilize a shared
  boundary or unblock other obligations, and then locality/context and verification cost.
  Do not invent numeric estimates or optimize solely for fewer agent calls.
- Group obligations when they share a root cause, implementation boundary, or substantial
  acceptance setup. Do not bundle unrelated work merely to avoid gates, and do not split a
  shared fix merely to preserve historical IDs. Keep packages small enough to review and deliver.
- Use existing dependency data as a lightweight graph. Do not introduce a DAG executor, planner
  agent, workflow engine, or parallel writer lanes without a demonstrated capability gap.
- After a coherent change, reassess pending obligations it may affect before commissioning
  another fix. Read changed shared boundaries and relevant acceptance evidence, not the whole
  repository again. Uncertain impact requires checking; locality is not proof of independence.
- A shared fix may close several authorized items with a separate acceptance map for each.
  Previously closed evidence must be reconsidered when a later change affects its guarantee;
  preserve historical evidence and repair regressions, rather than silently trusting old closure.
- Regressions introduced by the package and gaps in its own acceptance remain in scope. Track
  concrete unrelated discoveries only after parent approval; ask the owner before implementing
  work outside the authorized outcome. Do not falsely declare readiness with a new release blocker.

## Working method

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
- A status/documentation edit alone is not closure. Existing correct implementation with
  verified acceptance may justify a ledger-only update; new code is not a prerequisite.
  A resolved decision does not close any remaining implementation or verification work.
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
- Checkpoint commits are local-only and MUST NOT be pushed. Keep commits scoped; do not commit
  known-broken intermediate states. A completed autonomous goal requires a final local commit
  unless the user explicitly requests `no commit`.
- Only the parent may submit an accepted, locally gated and freshly reviewed candidate to hosted
  CI, by normal fast-forward push to the configured upstream; see the CI-backed Definition of
  Done below. A submission push is not completion. This does not authorize force-push, branch or
  remote changes, permission/secret changes, tags, releases, publication, or deployment.
- If a required CI-submission push is blocked by a missing upstream or rejection, preserve the
  candidate and report the blocker. Do not choose a remote or reconcile history automatically.
- Do not rewrite, squash, amend, rebase, reset, or otherwise alter existing user-authored
  history unless explicitly authorized.

## Pi orchestration

The parent session is the orchestrator.
`pi-subagents` is parent-only and must never appear in any child `skill` or `skills` field.

### Orchestration economy

Agent delegation is a scarce resource. Prefer direct parent inspection over
delegation unless a role is explicitly required below.

For each coherent package, not each historical item:

1. Parent performs bounded read-only assessment; use a scout only for a concrete discovery gap.
2. Launch `worker` with explicit `context: "fresh"` and a compact brief: authorized obligations,
   residual gaps, candidate, shared boundary, approved decisions, checks, and stop conditions.
   Retain that worker for implementation, review/browser fixes, evidence, and ledger updates.
   Start fresh for the next package to avoid accumulating the entire parent/goal history.
3. Require one fresh independent reviewer for the package's acceptance, including items proved
   already satisfied without code changes. Share setup and a review across items, not conclusions.
4. Recheck only the affected requirements and blast radius after fixes. Use a fresh review for
   material changes of direction or boundary; bounded rechecks may retain the reviewer. Aim for
   one initial review and one material re-review, not a hard cap that permits unresolved blockers.
5. Browser-test actual browser acceptance; oracle/researcher calls require a concrete decision
   or evidence gap. Do not repeat a consultation whose approved assumptions still hold.
6. Rotate a worker within a package only when unavailable or its context is demonstrably stale
   or contradictory. Checkpoint first and prove the prior writer and mutating descendants have
   stopped before launching a replacement. Never overlap writers or retry the same poisoned
   context indefinitely. Child Goal-mode status does not cancel a current explicit assignment.
7. Use native completion notifications for children. While awaiting CI or another external job,
   arrange a monitor/wake and use supported quiet waiting; avoid conversational status polling.
   Useful read-only next-package assessment may overlap a frozen candidate's verification.
8. Do not spawn agents to reconfirm existing adequate evidence. Reuse repository/version anchors
   and concise artifact references instead of copying entire transcripts, guides, or test logs.

### Single repository-content writer

This workflow overrides generic orchestration guidance that applies review fixes in the parent.
For backlog implementation and autonomous remediation:

- `worker` is the **only agent that edits repository content** in the active worktree.
- Repository content includes application code, tests, migrations, generated artifacts,
  normative documentation, closure ledgers, and any other tracked or untracked project file
  created or changed for the goal.
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
- Closure-ledger and goal-owned documentation updates MUST be delegated to `worker`; the
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

| Agent            | Purpose                                                                                             | Context | Allowed capability                                                                  | Must not do                                                              |
| ---------------- | --------------------------------------------------------------------------------------------------- | ------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `scout`          | Local repository reconnaissance, dependency tracing, test/contract discovery                        | fork    | read/search + supervisor contact                                                    | shell execution, writes, implementation, review fixes                    |
| `researcher`     | External/current documentation when repository evidence is insufficient                             | fresh   | read + web research + supervisor contact                                            | repository writes, implementation, broad research without a concrete gap |
| `worker`         | Initial implementation, all repository edits, accepted fixes, and optional local checkpoint commits | fresh   | read/search, shell validation, edit/write, local Git checkpoint, supervisor contact | make unapproved product/architecture decisions, push, spawn subagents    |
| `reviewer`       | Independent requirements/code review                                                                | fresh   | read/search + supervisor contact                                                    | shell execution, writes, implementation, applying fixes                  |
| `oracle`         | Decision-consistency check for S1/architecture/safety/concurrency/migration/recovery                | fork    | read/search, read-only shell inspection, supervisor contact                         | writes, implementation                                                   |
| `delegate`       | Lightweight read-only analysis when no specialist role fits                                         | fork    | read/search + supervisor contact                                                    | implementation, writes, replacing worker/reviewer/oracle                 |
| `browser-tester` | Independent browser-visible acceptance                                                              | fresh   | read/search, browser MCP, limited shell/runtime control, supervisor contact         | edit/write application code                                              |

The Context column is launch policy, not a claim that runtime defaults have been changed:
pass explicit fresh context for worker launches even when settings still default to fork.

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
the parent for backlog work. A review workflow may collect findings, but repository
fixes always go through `worker`.

### Delegation policy

Use the minimum number of agents needed for the authorized acceptance:

- `scout` only when direct parent inspection cannot efficiently establish the boundary; repeat
  only for a material discovery gap, not automatically for each item or package.
- `researcher` only for external facts that cannot be established from repository evidence.
- `oracle` before committing to an unresolved material architecture/safety/concurrency/migration/
  recovery decision. For core correctness/unsafe boundaries, explicitly establish the failure
  semantics and whether such a decision remains. Reuse an independently challenged direction
  only after confirming its assumptions still hold; touching a risky file alone is not a trigger.
- `worker` owns all package edits and accepted fixes, including tests, ledgers and documentation;
  coherent local checkpoint commits are allowed, never push.
- `reviewer` independently checks the package's obligations, not merely changed lines or a
  worker's attestation. No per-item fanout or full restart of reconnaissance for a narrow recheck.
- `browser-tester` exercises required browser-visible acceptance, including unchanged behavior
  claimed as already satisfied when existing evidence is inadequate.
- `delegate` only for bounded read-only support that does not fit another specialist.

Do not create recursive subagent fan-out. Ordinary child agents are not orchestrators.

## Independent review contract

Package review is requirements review, not merely code review.

Before approving closure, the reviewer must independently read:

1. every obligation proposed for closure in the package and its dependency plan;
2. its expected behavior and acceptance criteria;
3. applicable normative product contracts;
4. the current implementation, tests, and acceptance evidence at the identified candidate.

This also applies to no-code closure. Verify the existing implementation and evidence, not the
absence of a diff. One independent review may cover multiple obligations with distinct verdicts.

Reviewing only the diff is insufficient.

The reviewer must check:

- every acceptance requirement is implemented;
- negative/failure semantics match the contract;
- tests exercise the boundary where the prior behavior was wrong;
- every proposed closure is authorized and evidenced, including collateral obligations;
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
non-blocking for the package's acceptance contract.

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
When its recommendations conflict with `docs/product-spec.md`, the authorized acceptance
contract, existing Muzilla safety invariants, or accessibility requirements, the repository
contract wins.

For a package with material UI/UX scope:

1. Read its authorized acceptance and relevant product contract first.
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
with Muzilla unless the authorized outcome explicitly requires a broader redesign.

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

## Verification (project bindings)

Run the narrowest relevant checks while iterating. For each final package candidate, run the
backend gates below; also run frontend gates when `frontend/` is touched, and E2E when a user
journey, router, browser state or file flow changes. These commands and applicability rules are
local bindings, not hard-coded requirements for other projects.

Backend:

```bash
uv sync --locked --extra dev --extra audio
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
resources; HTTP health alone does not prove native capability. This project's native capability
gates include `fpcalc` and `rsgain`, as specified in the readiness contract.

Provider tests use deterministic contract fixtures, not live services. FTS5 tests use migrated
database fixtures. Tests that need build output create deterministic fixtures; ignored local
artifacts are not test inputs.

When OpenAPI changes, regenerate frontend types using the repository command and require the
generated result to be clean and in sync.

Database/schema work must include the applicable migration checks from
`docs/production-readiness.md`, including `alembic check` or its repository equivalent where
required.

## Acceptance evidence

Passing generic quality gates does not by itself prove that an obligation is complete.

Before a ledger closure or `goal_complete`, map every material authorized acceptance requirement
to concrete evidence. Existing checks can satisfy several obligations; do not duplicate tests
solely to give each item its own test file or execution.

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

Every completed obligation needs its own acceptance-to-evidence mapping, even when the parent
selected a multi-item package within an authorized full-backlog goal. Evidence must identify
revision/content, command and result, environment/image where relevant, and review/browser scope.
After changes, invalidate affected claims and rerun their checks. Reuse unaffected evidence only
with an explicit content/environment applicability check; unknown impact is not safe reuse.
Mandatory final-candidate gates and exact-SHA CI cannot be replaced by old green results.

Do not replace specific acceptance evidence with summaries such as "consistent", "looks green",
or "all tests pass".

## Autonomous goal contract (pi-goal)

An autonomous request may name one obligation or the whole outcome:

```text
/goal Close all outstanding obligations in <backlog>.
```

The execution contract comes from this file and the project's authoritative backlog, product
and readiness bindings. Do not require the owner to repeat it or choose each successive item.
Full-backlog mode uses one goal; packages are checkpoints, not nested goals or separate calls to
`goal_complete`. Do not narrow whole-goal success to the first delivered package.

### CI-backed Definition of Done

A goal is complete only after the exact final full commit SHA passes applicable local gates and
its latest hosted run of the project's designated CI workflow succeeds:

- Deliver coherent packages, not one candidate per historical item. Before submission, applicable
  local acceptance/readiness gates and independent review pass on that candidate; browser
  acceptance is also required where applicable. Run focused checks during repair, not the entire
  gate set after every edit. Run the complete applicable local gates on each final package candidate.
- Only the parent may submit by normal fast-forward push to the configured upstream. This is CI
  submission, not completion or authorization for force-push, branch/remote changes, permissions,
  secrets, tags, releases, publication, or deployment. Workers never push.
- The newest run for that exact 40-character SHA must have every project-required job successful;
  none may be pending, failed, missing, or skipped. Record run ID, URL, SHA and job results. Local
  checks or an older green run do not substitute. Verify delivery before starting the next writer
  package; useful read-only assessment may continue while CI runs.
- Every later candidate commit needs its own latest green hosted CI and applicable local gates.
  Review the new delta and affected acceptance; do not summon a new full review solely because
  the SHA changed. Never weaken a gate or expand scope/permissions to repair CI without authority.
- Record the ledger's verified implementation/content anchor, then attach final-commit CI evidence
  to the durable runtime handoff. Do not create another commit just to record the previous commit's
  CI URL/SHA and thereby invalidate that result. Ledger edits still require final-candidate gates.
- `no push` and `no commit` suppress only their delivery actions, not hosted CI. If the exact
  accepted content cannot be tested under that mode, the goal remains incomplete pending an
  explicit owner decision.
- At the end of a full-backlog goal, run all applicable final production-readiness gates on one
  final candidate/image, including integration evidence for previously closed guarantees. Package
  closure and green package CI alone do not certify the complete outcome.

### Goal preflight and execution

1. Establish authorized scope, artifact locations, normative acceptance, dependencies, risk,
   delivery mode and pre-existing worktree/index state. Do not invent work for an absent or
   already-completed requested item. For a whole backlog, select the next admissible package
   through the adaptive workflow, not a fixed ID sequence.
2. Assess current code/tests/evidence and record the residual before commissioning a fix.
   Reproduce the defect or establish a failing acceptance boundary before product edits;
   verification-only work may first add missing tests. Consult specialists only for concrete gaps.
3. Give the fresh worker the bounded package brief. Existing adequate implementation must not be
   rewritten to demonstrate activity. Retain this writer for all accepted fixes and ledger edits.
4. Obtain fresh independent package review and browser acceptance where needed. Parent adjudicates
   findings; worker fixes accepted blockers. Iterate focused checks and pertinent rechecks, subject
   to the convergence policy below. Do not review incomplete slices merely because an ID ended.
5. Audit every proposed closure, run applicable local gates, and let worker update justified
   ledger/documentation/generated content. Verify the exact resulting candidate and review the
   affected delta; no unconditional extra agent launch for a clerical update.
6. Finalize only accepted package content in the selected delivery mode. Parent may stage/commit;
   retain an existing accepted worker checkpoint as final HEAD instead of making an empty commit.
   Exclude unrelated changes. Hook mutations invalidate acceptance: return them to worker and
   rerun affected review/checks before corrected finalization. Do not rewrite history.
7. Parent submits and verifies exact-SHA CI. Record delivery evidence; reassess affected pending
   obligations and autonomously select the next package. If a package is blocked, assess independent
   authorized work without silently dropping it. Start another writer package only from a verified
   safe baseline with the prior writer stopped and no unfinished changes mixed into its candidate;
   otherwise keep assessment read-only and report the blocker.
8. When all authorized obligations are evidenced and all required final gates/review/browser/CI
   and delivery conditions pass, call `goal_complete` with the current goal ID. A full-backlog goal
   also needs final integration review of cumulative changes and readiness; reuse package verdicts
   with applicability checks rather than repeating each item's entire review independently.

### Goal completion evidence

`goal_complete` requires the exact current `goal_id` and a summary containing:

- authorized outcome and all obligations closed, including parent-selected packages within scope;
- acceptance-criterion-by-acceptance-criterion evidence;
- changed files;
- delivery mode: normal, `no push`, or `no commit`;
- final local commit SHA when a final commit is required or already represents the accepted
  candidate; otherwise the current `HEAD` plus explicit confirmation that the goal-owned changes
  remain uncommitted by user request;
- CI-submission push result and upstream/local SHA match when applicable; otherwise the explicit
  user override and skipped submission;
- relevant commands and exact pass/fail results;
- latest exact-SHA hosted CI run ID, URL, SHA, and required job results;
- independent reviewer result;
- browser acceptance result when applicable;
- generated-contract impact when applicable;
- migration/database impact when applicable;
- exact-image/runtime impact when applicable;
- residual risk.

For a large goal, cite the durable per-obligation audit and delivery receipts in the bounded
completion summary; retain every required evidence field without copying full logs/transcripts.
A reference must point to verified evidence, not substitute for a missing acceptance audit.

All required gates must apply to the exact goal-owned content being delivered. In normal or
`no push` delivery, that content is the final commit candidate. With `no commit`, it is the exact
accepted uncommitted goal-owned worktree content. Creating the final commit must not materially
change the tested candidate. If commit hooks or other commit-time actions change tracked
content, the candidate is invalidated and the affected gates must be rerun before delivery. Do
not create an empty commit when the completed candidate is already exactly represented by `HEAD`.

### Convergence, waits and stopping

- Compare residual obligations, failure fingerprints, changed content and new evidence at each
  worker/review/gate handoff. Progress means a requirement proved, a blocker resolved, or a
  materially useful diagnosis/decision; another tool call, plan, status poll, or identical test
  run does not itself count. Do not checkpoint on every tool call.
- Two consecutive repair/recheck cycles on the same residual without substantive progress trigger
  one bounded diagnosis. Continue only with a different testable direction and evidence; otherwise
  suspend that package, preserve its state and assess independent authorized work. Productive
  cycles may continue; an iteration target is not permission to ignore a concrete blocker.
- Separate complete, temporarily waiting, structurally blocked, no-progress, resource-limited,
  external-error and owner-decision outcomes. Completion requires proof, never exhausted turns.
  Genuine product/scope/permission decisions go to the owner, not repeated identical agents.
- Respect configured model/token/spawn/runtime limits; do not renew grants, remove caps, or switch
  execution protocols without authority. Record whether accounting covers parent and children;
  missing child usage is unknown, not zero. Before a known deadline/budget boundary, request a
  checkpoint at a safe tool boundary and launch no new work that cannot safely finish.
- Use native retries/backoff for transient infrastructure/provider errors; retry a child only
  after identifying the failure and preserving its partial state. Never treat infrastructure
  failure as product failure or rerun the whole package blindly. Wait on arranged external events
  rather than consuming model turns to poll; reattach to the same job/run after restart.
- Use only supported lifecycle controls and their actual contracts. `goal_blocked` requires a
  genuine evidenced external impasse and its required repeated turns; `goal_wait` requires an
  arranged external wake/deadline, not ordinary unfinished work. Do not manufacture three failed
  attempts, call unavailable pause tools, or claim a textual rule implements runtime termination.
  If safe immediate pause is unavailable, checkpoint, report the limitation and request owner
  intervention; do not launch further agents on the stalled package or falsely complete it.
- Never weaken acceptance, bypass dependencies, contact live providers, alter user-owned data,
  or silently broaden scope to avoid a stopped state.

### Checkpoint and resume

Keep one compact runtime checkpoint in existing session/mission storage outside repository
content; do not create a second backlog or permanent phase/recovery reports. At package handoffs,
material decisions, delivery and interruptions, retain:

- authorized scope and delivery mode; backlog reference; HEAD/candidate content identity;
- pre-existing changes excluded from ownership; package-owned tracked and untracked paths;
- active package, acceptance evidence/residuals, prerequisites and approved decisions;
- worker/session/run IDs, live-writer status, pending reviewer/browser blockers and last hypothesis;
- commands/results with revision, environment/image and durable artifact references;
- commit/push/upstream/CI run and required-job results; resource usage/coverage and stopping reason.

Use the host's available persistence; do not assume an unimplemented checkpoint tool exists.
Save a concise handoff through existing artifacts when structured session storage is unavailable.
Acceptance-critical evidence must not live only in auto-pruned outputs or temporary paths.
Repository/ledger and external verified state remain authoritative, not the checkpoint or memory.
On compaction/restart/interruption/voluntary resume, inspect HEAD, diff, ledger and live writers,
then reconcile receipts and query the exact pending CI/job. Invalidate incompatible evidence;
reattach/reuse the same work when valid. Never assume a completed child means accepted work,
launch a replacement writer with uncertain ownership, or redo delivered packages blindly.

## Readiness gates for autonomous completion

Delivery requires applicable gates on the exact final candidate, using commands/applicability
from the project verification bindings and additional gates from its readiness contract. Follow
the CI-backed Definition of Done; `no push` and `no commit` do not waive gates or exact-SHA CI.

- Generated contracts must be regenerated and clean/in sync when their source changes.
- Build prerequisites first. Use deterministic disposable fixtures, not live providers or
  ignored local build output as test inputs.
- Additional applicable gates include migrations/schema consistency, exact-image/native tools,
  recovery/restart, backup/restore, reset safety, scale/performance, secret handling and
  dependency/runtime audits. HTTP health alone does not prove native or recovery capability.
- During package delivery, run the complete applicable set for its changed boundary, not heavy
  unrelated gates for activity. Final full-backlog completion additionally requires the entire
  applicable production-readiness set and cumulative integration review on one candidate/image.

Previously valid final checks on the same content/environment may be reused with an explicit
applicability audit; different candidates or missing coverage require the relevant checks.
No package result, documentation edit or generic green suite alone certifies production readiness.

## Closure-ledger updates

Use the project's existing ledger and its retention policy from the project bindings. Parent
approves acceptance closure; worker edits repository content. A closure requires current
implementation, adequate proof for every requirement, no independent-review blocker, and local
acceptance/readiness gates. No new product code is required when already satisfied.

The worker may record acceptance-verified closure before submission so that ledger content is
part of the tested candidate. This is provisional delivery: exact-final-candidate checks and
hosted CI must still pass before accepting the package as delivered or completing the goal.

- Preserve historical findings/evidence where the ledger retains them; remove completed rows
  only where the ledger is defined to contain unfinished work exclusively.
- Keep unsatisfied criteria open. A proposed behavior in documentation, a touched file, or a
  dependent test passing does not establish closure or resolve a prerequisite.
- Map shared tests/reviews to each closed obligation; do not invent duplicate implementation,
  test suites, reviewers or ledger entries merely to match historical IDs.
- Acceptance-verified content pending exact-SHA CI is not delivered completion. Record that
  distinction in the runtime handoff and verify CI before proceeding; do not claim future results.
- Track concrete non-duplicative out-of-scope work only with parent-approved tracking and retain
  any owner decision needed to implement it. Do not indefinitely expand a full-backlog goal.

Git history, ledger evidence and delivery receipts are the record; no parallel closure ledger.

## Handoff

Report:

- authorized outcome, selected package and affected obligation IDs;
- product behavior changed;
- changed files;
- acceptance evidence;
- commands and results;
- reviewer result;
- browser/E2E result when applicable;
- migration/generated-contract/runtime impact;
- residual risk or remaining blocker;
- next authorized package/step or explicit stopping reason, without requesting another item
  selection during a full-backlog goal.
