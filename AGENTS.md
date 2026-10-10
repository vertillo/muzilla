# Muzilla agent guide

Durable repository guidance only. Keep this file concise and current; do not create parallel
plans, histories, copied prompts, or temporary implementation reports. Git history is the
historical archive.

## Sources of truth

Before changing behavior or architecture, read in order:

1. `docs/product-spec.md` — normative product behavior and interaction model.
2. `docs/code-review-2026-10-04.md` — active findings, acceptance, dependencies, and Docker
   evidence. Preserve diagnoses/history; update finding status and closure evidence in place.
3. `docs/completion-matrix.md` — exhausted original backlog; do not repopulate with review findings.
4. `docs/production-readiness.md` — durable completion and release-candidate gates.
5. `README.md` — operator-facing product and deployment overview.
6. Current code and tests — evidence of actual implementation.

When code and the product specification differ, do not hide the discrepancy: implement the
relevant completion obligation or update the specification only if the intended contract deliberately
changed. Hermes and other agent memory are historical context, never authority over repository
contracts and evidence.

## Product and safety invariants

- Muzilla manages metadata, not playback or a listening library. Libraries may be flat and poorly
  tagged; files are the source of truth and the database detects external drift.
- Scans, provider fetches, candidate selection, edits, and review decisions never write music.
  Every file mutation uses the reviewed apply/recovery path with containment, preconditions,
  journal/recovery, and an explicit per-file result.
- ReviewBundle is the sole target review model and is atomic by default. A bundle ends fully
  applied, fully restored, or in an explicit fail-closed recovery state; partial success is not an
  accepted final outcome. Technical jobs remain separate; do not claim a global filesystem
  transaction.
- There is no auto-apply. Strong candidates may be preselected but require explicit user Apply;
  ambiguous candidates need explicit choice or Skip, unresolved ambiguity blocks the bundle, and
  rejected candidates remain hidden/unselectable/unforceable.
- The web UI is the only surface that applies metadata/file changes. The CLI is support tooling
  and never applies or auto-applies. Grouping is internal inference, not arbitrary cross-album
  reassignment or a primary user model. Current, proposed, and attempted state remain distinct.
- Preserve auth, CSRF/origin checks, security headers, proxy/path/symlink defenses, non-root
  containers, reset safety, and secret redaction unless an equal or stronger replacement is
  verified.

## Architecture boundaries

Import-linter contracts are authoritative. Intended direction (a permitted subset, not a request
for additional imports):

```text
domain          → nothing
tags, paths     → domain
providers       → domain, db
matching        → domain, providers
audio           → standalone (no muzilla imports)
db              → domain
changes         → domain, db, tags, paths
pipeline        → lower layers
jobs            → lower layers, pipeline
services        → lower layers, jobs
api, cli        → services (plus config/logging support)
```

Additional rules: `domain/fields.py` is the canonical field registry; API/CLI do not import DB
models; Mutagen and SQLAlchemy stay synchronous, with async at HTTP/worker boundaries; large
sorted collections use cursor/keyset pagination, not OFFSET; provider search summaries differ
from hydrated candidate details; frontend server types come from generated OpenAPI types and view
adapters do not recreate the API schema.

## Scope, assessment, and packaging

- `docs/code-review-2026-10-04.md` is the active backlog/closure ledger; `docs/completion-matrix.md`
  contains unfinished work only and is currently exhausted. Hosted CI is
  `.github/workflows/ci.yml` with required `backend`, `frontend`, `e2e`, and `docker` jobs.
  Explicit finding dependencies and phase prerequisites apply; P1/P2/P3 are not S1/S2/S3 risk
  labels.
- Use one parent goal for the authorized outcome. The obligation is the accounting unit; the
  smallest coherent change is the implementation/review unit. A full-backlog request authorizes
  the parent to select admissible coherent packages, including later findings already open at
  kickoff; it does not authorize an unbounded stream. A narrow request leaves unrelated findings
  open.
- Before implementation, record the initial scope/worktree state and map each selected acceptance
  requirement to current evidence or a concrete residual at an identified revision. Treat old
  diagnoses and suggested fixes as leads, not instructions. If behavior/evidence suffice, do not
  rewrite it; if proof alone is missing, verify or add only the needed regression; otherwise fix
  the residual. A green generic suite or touched file is not proof.
- Honor explicit/phase prerequisites; do not infer ordering from IDs. Verify prerequisite closure
  before dependent work; an out-of-scope prerequisite blocks that work, not permission to absorb it.
  Select by residual need, risk, shared-boundary leverage, then locality/context and verification
  cost; do not invent numeric estimates or optimize only for fewer agent calls. Group shared-cause,
  boundary, or acceptance work; do not overbundle unrelated items or split shared fixes by ID. Use
  dependency data lightly. The optional read-only planner role below is advisory, not an automated
  planner/workflow engine; adding such capability still requires a demonstrated capability gap, and
  parallel writer lanes remain prohibited.
- After each coherent change, reassess affected open and previously closed obligations. Read the
  changed shared boundary and relevant evidence, not the whole repository; locality alone is not
  proof of independence. Shared fixes need a separate acceptance map per satisfied obligation;
  collateral satisfaction is not automatic closure. Preserve historical evidence and repair
  regressions. Package regressions and its acceptance gaps remain in scope. Track unrelated
  findings only with parent approval; never hide a release blocker.
- Closure without product code is valid when current behavior and evidence satisfy every criterion
  and independent review agrees. Never weaken acceptance or remove behavior to close a finding.
  Genuine product/UX ambiguity needs an owner decision; track its consequences through implementation
  and verification under Repository safety and implementation.

## Repository safety and implementation

- Before coding, reproduce the defect or add a failing test at the wrong boundary. Do not weaken or
  rewrite tests to make current behavior green. User-observed behavior is primary visible evidence;
  preserve it as automated acceptance or a precise manual check. State expected behavior and
  failure semantics before changing a critical boundary.
- Prefer small migrations/adapters. Track each compatibility layer with a completion/retirement
  obligation and exit condition. Track genuine product/UX choices until consequences are implemented
  and verified; record resolved contract changes in normative documentation. A decision or
  status/documentation edit alone is not closure.
- Leave unrelated worktree and index state exactly as found. Never use `git restore`, `git checkout`,
  `git reset`, `git clean`, or equivalents to obtain a clean/goal-scoped tree. Exclude unrelated
  paths from inspection, staging, commits, and finalization. Pre-existing `.pi/` changes are
  user-owned runtime configuration; never restore, normalize, or edit them unless the user
  explicitly requests that exact change.
- Treat all repository-local `music/` contents as a fully disposable test sandbox; agents may
  freely inspect, create, modify, move, corrupt, or delete its contents for tests, E2E, apply/undo,
  recovery, migrations, and destructive safety checks. Preserve only content a specific test needs.
  This authority does not extend to music outside that sandbox or user-owned data. Never inspect,
  import, modify, reset, or delete external/user-owned music. Do not use real `data/`, secrets,
  backups, `.env*`, or other user-owned state as fixtures.
- During autonomous goal execution, only the active worker may make a local checkpoint commit when
  it improves recoverability, reviewability, or continuation; keep it scoped and never commit a
  known-broken state or push it. An explicit `no commit` forbids checkpoints; a completed goal
  requires a final local commit unless the owner explicitly requests no commit. Never rewrite,
  squash, amend, rebase, reset, or otherwise alter user-authored history without authorization. Only
  the parent may submit an accepted candidate by normal fast-forward push to the configured
  upstream; this does not authorize force-push, remote/branch changes, permission or
  secret changes, tags, releases, publication, or deployment. Preserve and report a blocked push;
  do not choose a remote or reconcile history automatically.

## Orchestration, roles, and review

The parent orchestrates; `pi-subagents` is parent-only and must not appear in a child's `skill(s)`.
Independent read-only analyses may run in parallel, but there is one repository-content writer:
the active `worker`.

### Package lifecycle and writer

- For each coherent package, use bounded read-only assessment; use `scout` only for a concrete
  discovery gap. Launch one `worker` with explicit fresh context and a compact brief of scope,
  residual, candidate, decisions, checks, and stop conditions. Retain it through implementation,
  accepted reviewer/browser fixes, evidence, and ledger updates for that same package. An
  independently scoped new request is a new assignment requiring a fresh worker and compact
  verified brief, even if it touches the same file, follows a safe stop, or is documentation/config
  work; do not resume the old writer's full history merely because the repository or role is the
  same. If the worker cannot be launched, stop repository mutation and report the blocker; never
  fall back to parent-authored edits.
- Only the active worker edits repository content, including code, tests, migrations, generated
  files, docs, ledgers, and goal-owned artifacts. Parent/reviewer/browser/scout/oracle/delegate
  do not write repository files. The parent may inspect and verify; after acceptance it may stage,
  commit, push, and verify delivery. Unexpected commit-hook content changes invalidate acceptance
  and return to the worker for repair/recheck.
- Accepted review/browser findings and closure-ledger/documentation edits for an open package
  return to its worker. One writer means one at a time, not one indefinitely. Do not launch
  overlapping writers. Within a still-open package, rotate only if its worker is unavailable or
  demonstrably stale; before replacement, checkpoint and prove the previous worker and mutating
  descendants stopped, then reconcile the exact candidate, owned and pending paths, and handoff so
  unfinished ownership is not lost or mixed with another scope. This rotation limit does not bar a
  fresh worker for an independently scoped assignment. A reviewer BLOCK is a worker handoff, not
  permission for another role to fix files.
- Require one fresh independent requirements review per package, including no-code closures; share
  setup across obligations, not conclusions. Recheck affected acceptance only; get a fresh review
  after a material direction/boundary change. Aim for one initial and one material re-review, not
  as a cap that permits blockers. Use browser/oracle/researcher only for a concrete acceptance or
  decision/evidence gap; do not repeat a consultation whose assumptions still hold or summon agents
  to reconfirm adequate evidence. A child's Goal-mode status does not cancel its explicit assignment.
- Use native child completion notifications and supported quiet waits; arrange a supported monitor/
  wake while awaiting external CI rather than conversational polling. During CI for a prior frozen
  candidate, apply the independent-package safe-baseline rule under Autonomous goal delivery >
  Candidate gates and CI.

### Roles and capabilities

Runtime tool allowlists in `.pi/settings.json` are ceilings; do not broaden them to avoid a handoff.

| Agent | Purpose and allowed capability | Must not do |
| --- | --- | --- |
| `scout` (fork) | Local read/search; supervisor contact | Shell, writes, implementation, review fixes |
| `researcher` (fresh) | External research when repository evidence is insufficient | Repository writes, implementation, broad gapless research |
| `worker` (fresh) | Read/search, shell validation, edits, local checkpoint commits, supervisor contact | Unapproved product/architecture decisions, push, subagents |
| `reviewer` (fresh) | Independent read/search review; supervisor contact | Shell execution, writes, implementation, applying fixes |
| `oracle` (fork) | Read/search and read-only shell analysis; supervisor contact | Writes or implementation |
| `delegate` (fork) | Bounded read-only analysis; supervisor contact | Writes, implementation, replacing a specialist |
| `planner` (fresh) | Bounded read-only analysis of acceptance, dependencies, residuals, and package sequencing; read, grep, find, ls, symbol_search, module_report, read_symbol, read_enclosing, project_report; supervisor contact only | Shell, writes, implementation, package selection, orchestration, ledger closures, binding decisions/approvals, push, subagents |
| `browser-tester` (fresh) | Read/search, browser MCP, limited shell/runtime control; supervisor contact | Application-code edits |

Contexts are launch policy; use explicit fresh context for workers even if runtime defaults differ.
Documenting `planner` does not install or enable it; verify runtime capability before launch. The
parent retains scope, package selection, orchestration, and approvals and launches it only for
concrete planning/sequencing gaps—not routine or mandatory full-backlog planning—with explicit fresh
context and a compact verified-state/contracts/dependencies/approved-decisions brief, never inherited
history.
Oracle's fork is intentional and its shell access is read-only. Use only the minimum roles needed:
researcher for external facts; oracle before unresolved material architecture/safety/concurrency/
migration/recovery decisions. For unsafe boundaries, establish failure semantics and whether a
decision remains; reuse a challenged direction only after confirming its assumptions. Use `delegate`
only for bounded work that fits no specialist. Do not create recursive fan-out. The parent uses
`council-mode` only when multiple perspectives are necessary, not for routine review. Attach skills
through the native harness with `inheritSkills: false` by default; other specialist skills must be
needed and role-appropriate. Browser testers use only their explicit project skill, not unrelated
skills. Do not use `/parallel-review ... autofix`; all repository fixes go through the worker.

### Independent review

Before approval, the reviewer independently reads each obligation/dependency, acceptance criteria,
applicable product contract, and current implementation/tests/evidence at the candidate. Reviewing
only the diff is insufficient. Check positive and failure semantics, tests at the prior defect
boundary, authorized closure, architecture/product invariants, and applicable migration, generated
contract, persistence, restart, concurrency, security, and recovery consequences. Reviewer is
read-only and does not run tests; report concrete findings and the smallest fix. Material
correctness, safety, acceptance, security, migration, or architecture findings block. `OK with
notes` is acceptable only for demonstrably non-blocking notes.

### UI/UX and browser acceptance

For browser-visible UI/UX implementation or material review, attach `ui-ux-pro-max` to the worker
and reviewer, and to the browser tester when needed. Use its search/design workflow for applicable
patterns, accessibility, anti-patterns, and stack guidance. Read the authorized acceptance and
product contract; adapt the existing design language; accessibility and repository contracts
prevail. Do not introduce a new visual system, component library, or interaction paradigm merely
from generic advice. Use `browser-tester` for changed user journeys, routing, browser state,
interactions, responsive behavior, or file flow, and unchanged browser behavior claimed satisfied
when existing evidence is inadequate. It must exercise the browser and report flow,
expected/observed behavior, console/runtime errors, failed requests, reproduction, and PASS/FAIL.
Browser PASS does not replace other gates; accepted fixes return to the worker.

## Verification, evidence, and closure

Run focused checks while repairing. On a frozen final package candidate, run every applicable local
gate and independent review; frontend gates apply when `frontend/` is touched, and E2E when a user
journey, router, browser state, interaction, or file flow changes. Documentation-only deltas need
focused diff/consistency checks, not full backend, browser, or runtime suites solely because prose
changed. Exact-SHA hosted CI remains mandatory for final delivery.

Backend:

```bash
uv sync --locked --extra dev --extra audio
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

Frontend (`cd frontend`): `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`.
E2E (`cd e2e`): `npm run test` when applicable.

- Docker/native/deployment work uses the exact built image and isolated Compose resources; HTTP
  health alone does not prove native capability. Native gates include `fpcalc` and `rsgain`, per
  the readiness contract.
- Build prerequisites first. Provider tests use deterministic contract fixtures, not live services.
  FTS5 tests use migrated DB fixtures. Tests needing build output create deterministic fixtures, not
  ignored local artifacts.
- OpenAPI changes require regeneration and a clean/in-sync frontend type comparison. DB/schema
  changes require applicable migration checks, including `alembic check` or repository equivalent.
- Delivery readiness also applies relevant recovery/restart, backup/restore, reset, scale/performance,
  secret-handling, dependency/runtime-audit, and exact-image gates from `docs/production-readiness.md`.
  Full-backlog completion requires all applicable final readiness gates and cumulative integration
  review on one final candidate/image.

Passing generic gates alone does not close obligations. Evidence may include focused regressions,
integration/browser runs, disposable fixtures, migration/recovery/restart checks, exact-image or
generated-contract proof, command output, or direct structural evidence. Map each material criterion
to evidence anchored to revision/content and command/result, with environment/image and reviewer/
browser scope as relevant. Existing checks may support multiple criteria; do not duplicate tests
solely by item.
Applicability-audited final checks on identical content and environment may satisfy frozen-candidate
gates. After a change, invalidate and rerun only affected checks; reuse unchanged evidence only after
explicit content/environment applicability checks. Unknown impact requires checking. Stale results
or older CI cannot substitute for current gates; the latest exact-SHA hosted CI remains mandatory.
Do not summarize evidence as merely "consistent", "green", or "all tests pass".

For ledger closure, parent approves; worker edits the active ledger and preserves its retention
policy. Closure requires current behavior, adequate criterion-mapped proof, independent review,
and applicable local gates. Preserve history where retained; remove completed rows only when the
ledger is unfinished-work-only. Keep unsatisfied criteria open; a proposed behavior, touched file,
doc edit, or dependent test alone is not closure or prerequisite resolution. Map shared evidence to
all closed obligations without duplicating tests/reviews. Closure before hosted CI is provisional;
record that distinction. Track out-of-scope work only with parent approval.

## Autonomous goal delivery

A whole-backlog request is one goal; packages are checkpoints, not separate goals. Do not narrow
whole-goal success to the first package. Establish initially open obligations, scope, dependencies,
delivery mode, artifact locations, and pre-existing worktree/index state. Do not require the owner
to select the next authorized package or invent work for an absent/completed obligation.

### Candidate gates and CI

- Freeze code, tests, and ledger before final gates. Apply the gate applicability, documentation-only,
  evidence-reuse, independent-review, and browser-acceptance rules in Verification, evidence, and
  closure to that frozen candidate.
- Only the parent may submit by normal fast-forward push to the configured upstream. The latest
  workflow run for the exact 40-character final commit SHA must have every required job successful;
  none may be pending, failed, missing, or skipped. Record run ID, URL, SHA, and job results. Local
  checks or older CI do not substitute.
- An independent package may start during CI only from a verified safe baseline after the prior
  candidate is frozen and its writer stopped; keep its changes separate and do not mix them into
  that candidate. Pending CI is not delivery. Do not claim the candidate delivered or start dependent
  work until exact-SHA CI passes.
- Every later candidate commit requires its own applicable local gates and latest green exact-SHA CI.
  Review the delta and affected acceptance; do not repeat full review solely because SHA changed.
  Record the verified implementation/content anchor and CI receipt without an extra commit that
  invalidates CI. Never weaken gates or permissions to repair a failure.
- `no push` and `no commit` suppress only those delivery actions, not exact-SHA CI. If exact accepted
  content cannot be tested under that mode, the goal remains incomplete pending owner decision.
- Full-backlog completion follows the cumulative one-candidate readiness gates in Verification,
  evidence, and closure and `docs/production-readiness.md`; package closure or CI alone is insufficient.

### Execution and completion

1. Assess the authoritative backlog/dependencies and current code/tests/evidence; record residuals
   before commissioning fixes. Reproduce defects or establish failing acceptance before product edits.
2. Verify prerequisites; select a coherent authorized package. Existing adequate behavior is not
   rewritten to demonstrate activity.
3. Follow the package lifecycle and independent-review/browser rules above; parent adjudicates
   findings and the worker fixes accepted blockers.
4. Reassess affected obligations, map each to evidence, update only justified ledger/docs, freeze the
   candidate, and run the applicable final gates/review.
5. Parent finalizes only accepted package content. Exclude unrelated changes; if hooks mutate tracked
   content, return it to the worker and invalidate affected review/checks. Do not make an empty commit
   when accepted content is already exactly at HEAD; do not rewrite history.
6. Parent submits the frozen candidate and tracks exact-SHA CI. While it runs, reassess pending
   obligations; apply the safe-baseline rule in Candidate gates and CI for any independent package.
   Verify CI before claiming delivery or starting dependent work. If blocked, preserve state and
   assess independent authorized work without mixing candidates.
7. Complete only after all authorized obligations and applicable final gates, review, browser, CI,
   and delivery requirements pass; call `goal_complete` with the exact current goal ID.

`goal_complete` evidence includes: authorized outcome and every obligation/criterion map; changed
files; delivery mode; final SHA or current HEAD plus explicit no-commit owner override; push result
and upstream/local SHA match or explicit authorized skip; exact commands/results; latest exact-SHA
CI run ID/URL/SHA/jobs; reviewer and browser results; generated-contract, migration/database, and
image/runtime impact; residual risk. Large goals may cite verified durable per-obligation audits
and delivery receipts instead of copying logs. References must point to evidence, not substitute for
it. With no commit, gates apply to exact accepted uncommitted content; any commit-time content change
invalidates affected checks.

## Convergence, stopping, and resume

- At each handoff compare residuals, failure fingerprints, changed content, and new evidence.
  Progress means a requirement proved, blocker resolved, or materially useful diagnosis/decision;
  extra calls, plans, status polls, or identical runs are not progress.
- Two consecutive repair/recheck cycles on the same residual without substantive progress trigger one
  bounded diagnosis. Continue only with a new testable direction/evidence; otherwise suspend the
  package, preserve state, and assess independent authorized work. Do not ignore productive work or
  a concrete blocker to meet an iteration target.
- Distinguish complete, waiting, structurally blocked, no-progress, resource-limited, external-error,
  and owner-decision states. Completion requires proof. `goal_wait` needs an arranged external
  wake/deadline; `goal_blocked` needs a genuine evidenced external impasse and its required repeated
  turns. Never manufacture failed attempts or repeated turns, invoke unavailable lifecycle controls,
  or claim prose enforces runtime termination.
- Respect configured resource limits; do not renew grants, remove caps, or switch protocols without
  authority. Record whether accounting covers parent/children; unknown child usage is not zero. Before
  a known budget/deadline boundary, checkpoint at a safe tool boundary and launch nothing unable to
  finish safely. Use native retry/backoff for transient errors; identify failures and preserve partial
  state before retrying. Never blindly rerun a package or treat infrastructure failure as product
  failure. Wait on arranged events instead of polling; reattach to the same job/run.
- If safe pause is unavailable, checkpoint, report the limitation, request owner intervention, and
  launch no further agents on the stalled package. Never weaken acceptance, bypass dependencies,
  contact live providers, alter user-owned data, or silently widen scope.
- Keep one compact runtime checkpoint in existing session/mission storage outside repository
  content; use supported host persistence and do not assume an unavailable tool. Do not create
  another backlog or permanent recovery report. At handoffs/interruption retain
  scope/delivery mode, backlog, HEAD/candidate, pre-existing and owned paths, active package/residuals/
  prerequisites/decisions, worker/session/run IDs and live status, reviewer/browser blockers and last
  hypothesis, commands/environment, evidence paths, resource-coverage state, commit/push/upstream/CI,
  and stopping reason. Acceptance-critical evidence must not live only in auto-pruned output or temp
  paths.
- On resume, inspect HEAD, diff, ledger, live writers, and the exact pending CI/job; reconcile
  receipts and invalidate incompatible evidence. Reuse only applicable evidence. Do not replace a
  writer until it is stopped and ownership is certain; do not redo delivered packages blindly.

## Handoff

Report authorized outcome, selected package/obligations, behavior changed, files, criterion-mapped
evidence, commands/results, reviewer and browser/E2E verdicts when applicable, migration/generated/
runtime impact, residual risk/blocker, and next authorized step or explicit stopping reason. Do not
claim goal completion without all required evidence and delivery gates.
