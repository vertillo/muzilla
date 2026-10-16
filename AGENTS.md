# Muzilla agent guide

Durable repository guidance only. Keep this file concise and current; do not create parallel
plans, histories, copied prompts, or temporary implementation reports. Git history is the
historical archive.

## Sources of truth

Read only the contracts and evidence relevant to the assigned task before judging or changing
behavior; no fixed document stack applies to every task:

- `docs/product-spec.md`: normative product behavior and interaction. If code differs, report the
discrepancy—code/tests show actual behavior. Implement the relevant obligation, or change the spec
only when the intended contract deliberately changed.
- `docs/code-review-2026-10-04.md`: active review ledger for assigned findings, acceptance,
dependencies, and evidence. Preserve diagnoses/history; update status and closure evidence in place.
- `docs/completion-matrix.md`: exhausted original backlog, not a place for review findings; consult
only when original backlog/history matters.
- `docs/production-readiness.md`: applicable release/readiness gates; read for those gates.
- `README.md`: operator-facing product/deployment overview; read for operator or deployment work.
- Current code and tests: actual implementation behavior and evidence.

Inherited assistant plans/history and agent memory are context, never authority over repository
contracts or evidence.

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

- `docs/code-review-2026-10-04.md` is the active review ledger; `docs/completion-matrix.md` is the
  exhausted unfinished-original-work list. Hosted CI (`.github/workflows/ci.yml`) requires
  `backend`, `frontend`, `e2e`, and `docker`. Honor explicit finding dependencies and phase
  prerequisites; P1/P2/P3 are not S1/S2/S3 risk labels.
- One parent goal covers the authorized outcome. Finding IDs and their criteria are acceptance and
  reporting units, not mandatory separate assignments; the parent may batch findings only around
  shared causes, contracts/boundaries, or acceptance evidence and keeps a criterion-to-evidence map
  for each. Packages remain the smallest coherent change/review. Full-backlog scope lets the parent
  select admissible packages, including later findings open at kickoff, but not an unbounded stream.
  Narrow scope leaves unrelated findings open.
- The parent owns current-evidence assessment, package/residual selection, approved direction/failure
  semantics, gate applicability, and closure approval. Record initial scope/worktree state and map
  each criterion to evidence or a concrete residual at an identified revision. Treat old diagnoses
  and fixes as leads. If behavior/evidence suffice, do not rewrite; if only proof is missing, verify
  or add only needed regression evidence; otherwise fix the residual. Generic green suites or touched
  files alone are not proof.
- Verify explicit/phase prerequisites before dependent work; do not infer order from IDs or absorb
  an out-of-scope prerequisite. Select by residual need, risk, shared-boundary leverage, then
  locality/context and verification cost; invent no numeric estimates or optimize only for fewer
  calls. Group only findings with a shared cause, contract/boundary, or acceptance need; do not
  overbundle unrelated findings or split shared fixes by ID. Use dependency data lightly. The optional
  read-only planner is advisory, not an automated planner/workflow; require a demonstrated capability
  gap to add such capability. Keep the selected plan unless new evidence changes dependencies, scope,
  or approved direction; consult planners or other advisers only for a concrete gap. Use the sequential
  default; concurrent implementation lanes are permitted only under Package lifecycle and writer.
- In an authorized package, the parent may authorize the smallest bounded correction for a concrete
  discovered bug without separate owner approval only when no product/security decision is needed
  and existing permissions suffice. Already-permitted technical, test, and lifecycle work needs no
  owner approval. This grants no new features, unrelated follow-ons, or safety/permission bypasses;
  preserve prerequisites/evidence and escalate genuine product, security, scope, resource, or
  permission blockers.
- After each coherent change, reassess affected open and previously closed obligations using the
  changed shared boundary and relevant evidence, not the whole repo; locality alone is not proof of
  independence. Map shared fixes to each satisfied obligation—collateral closure is not automatic.
  Preserve history, repair regressions, and keep package regressions/gaps in scope. Track unrelated
  findings only with parent approval; never hide a release blocker.
- No-code closure is valid only when current behavior/evidence meet every criterion and independent
  review agrees. Never weaken acceptance or remove behavior to close findings. Genuine product/UX
  ambiguity needs an owner decision (see Repository safety and implementation).

## Repository safety and implementation

- Before product edits, reproduce the defect or establish failing acceptance at the prior defect
  boundary; documentation-only prose changes need no artificial test. Do not weaken or rewrite tests
  to make current behavior green. User-observed behavior is primary visible evidence;
  preserve it as automated acceptance or a precise manual check. State expected behavior and failure
  semantics before changing a critical boundary.
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
- Routine diagnostics and test/process lifecycle or recovery are agent-owned within existing scope
  and permissions. Exhaust safe authorized alternatives before asking the owner to run ordinary
  commands or approve routine continuation; children escalate technical handoffs to the parent.
  Preserve genuine permission, resource, scope, product, and safety blockers; fail closed rather
  than promising unconditional continuation or bypassing permissions.
- On macOS, if `ps` is denied, use already permitted libproc through Python stdlib `ctypes` in an
  inline or temporary shell command (optionally `tools.bash` from codemode); codemode itself has no
  native-library, Node, or filesystem access. Track launched PIDs/PPIDs/PGIDs and start identities;
  enumerate safely, handle races and truncation, and treat unknown as unknown. Prove ownership and
  termination including descendants; a dead runner or empty filename grep is not proof. Never kill
  arbitrary or unrelated processes or mask uncertainty.
- Keep test runtimes in managed commands with teardown, not uncontrolled detached services. Native
  `subagent_command` controls are unavailable for pi-sandbox custom bash unless that backend offers
  compatible controls; do not instruct enabling a missing tool or removing the sandbox.
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
Independent read-only analyses may run in parallel. Implementation defaults to one lane; at most two
lanes may run concurrently only in separate verified worktrees under the independence and ownership
rules below. Each worktree, including the integration checkout, has at most one repository-content
writer.

### Package lifecycle and writer

- Before assignment, the parent selects the worker pair: Luna (`openai-codex/gpt-6-luna`, `max`)
  by default, or Sol (`openai-codex/gpt-6.1-sol`, `medium`) when the package requires reasoning
  across architecture boundaries with nonlocal effects, analysis of interdependent concurrency,
  migration, recovery, or security failure modes, or reconciliation of contradictory contracts,
  code, or evidence for which no bounded fix is established. File/finding counts and test duration
  alone do not justify Sol; well-defined local fixes at critical boundaries may remain with Luna.
  Before launch, the parent briefly states the selection basis. Selection grants no additional
  decision authority and does not replace required consultation or independent review. Verify the
  exact resolved pair and stop on mismatch; do not fall back automatically or change model/effort
  during the active package, including resumes or replacements. Workers do not invoke subagents or switch
  models; any replacement remains subject to the single-writer and handoff rules below.
- For bounded read-only fact-gathering, the parent may read directly; no scout or fan-out is
  mandatory. Use `scout` only for a concrete local discovery gap, `researcher` only for needed
  external facts, and `planner` only for a concrete gap in its existing advisory role. Collection
  roles return cited facts and open questions; the parent synthesizes assessment and decisions.
- For each coherent package, normally launch one worker with explicit fresh context; use one worker
  per lane if a concurrent pair is authorized below. Give each a compact, cold-start-complete brief:
  obligation/grouping; baseline revision and assigned worktree; exclusive owned and pre-existing
  paths, including tests, fixtures, and generated files; isolated runtime resources for concurrent
  lanes; concrete residuals and relevant evidence; approved direction and failure semantics;
  applicable contracts and source anchors; scope/non-goals/permissions; criterion-level success
  checks and stop/escalation conditions. Distinct filenames alone do not establish independence.
  Include validation only when it establishes a residual or is required for acceptance. For Sol 6.1,
  make the brief concise and outcome-oriented; for Luna, state bounded scope, relevant evidence,
  success criteria, and stop conditions explicitly. Do not copy transcripts, obsolete diagnoses,
  command itineraries, or repeated global rules; runtime options supply managed
  output/read/progress/acceptance details. Retain each worker through implementation, accepted
  reviewer/browser fixes, and criterion evidence; the designated integration worker updates common
  files and the ledger only after other lane writers stop. An independently scoped new request needs
  a fresh worker and compact verified brief, even if it touches the same file, follows a safe stop,
  or is documentation/config work; do not resume the old writer's full history. If a worker cannot be
  launched, stop repository mutation and report the blocker; never fall back to parent-authored edits.
- Only assigned worker(s) edit repository content, including code, tests, migrations, generated
  files, docs, ledgers, and goal-owned artifacts. Default to one lane; at most two workers may run
  concurrently, each in a separate verified isolated worktree with exclusive paths (including tests,
  fixtures, and generated outputs) and separately isolated runtime resources. The parent authorizes
  concurrency only after verifying semantic independence, satisfied prerequisites, and capacity
  within configured limits; disjoint filenames alone are insufficient. Shared API/schema/contracts,
  fixtures, or non-isolatable runtime resources require sequential work. If the runtime cannot provide
  the isolation or the source worktree is unsuitable, proceed sequentially without cleaning
  pre-existing changes, changing permissions, or installing tools. No worktree, including the
  integration checkout, has concurrent writers. Parent/reviewer/browser/scout/oracle/delegate do not
  write repository files. Common files, including the ledger, belong to integration: after all other
  lane writers and their mutating descendants are verified stopped, one worker authorized for
  integration reconciles lane contributions and updates common files. The parent decides, verifies,
  and finalizes but does not edit their contents. The parent may inspect and do read-only provenance,
  hash, ownership, or evidence analysis; after acceptance it may stage, commit, push, and verify
  delivery. Unexpected commit-hook content changes invalidate acceptance and return to the worker
  for repair/recheck.
- One writer per worktree, not one writer globally; concurrent lanes are allowed only under the
  isolation rules above, and never overlap writers in the same worktree. Within a still-open package,
  rotate only if its worker is unavailable or demonstrably stale; assess staleness when contradictory
  or superseded context requires rediscovery, not by token or finding count. Before replacement,
  checkpoint and prove the previous worker and mutating descendants stopped, then reconcile the exact
  candidate, owned/pending paths, and handoff so unfinished ownership is not lost or mixed. A reviewer
  BLOCK is a worker handoff, not permission for another role to fix files.
- Require one fresh independent requirements review for each integrated package/batch, including
  no-code closures; the initial review covers all assigned obligations, dependencies, criteria,
  applicable contracts, implementation, and evidence. The reviewer returns aggregated feedback,
  distinguishing blockers from notes. Re-review the delta, prior blockers, and acceptance affected by
  the changed boundary, explicitly reusing only earlier review that remains applicable. A material
  direction/boundary change requires a fresh independent review adequate to the changed scope. Aim
  for one initial and one substantive re-review, not as a cap that permits blockers. Use
  browser/oracle/researcher only for a concrete acceptance or decision/evidence gap; do not repeat a
  consultation whose assumptions still hold or summon agents to reconfirm adequate evidence. A child's
  Goal-mode status does not cancel its explicit assignment.

### Roles and capabilities

Runtime tool allowlists in `.pi/settings.json` are ceilings; do not broaden them to avoid a handoff.
Current configured models and tools remain authoritative; role-specific limits here specialize generic
builtin prompts and never grant tools or permissions. Read-only child roles return findings normally;
if an artifact is required, the runtime or parent manages it outside the repository using supported
output/progress controls consistent with the role. Do not ask to add tools solely to write output or
create repository context, progress, or research reports. Worker progress, when requested, also stays
outside the repository. Do not invent direct runtime options.
Use each role's owner-configured model and reasoning effort from `.pi/settings.json` for every
launch, workflow child, and resume. Only for the worker pair selected under Package lifecycle and
writer may the parent use per-execution model/thinking overrides; other roles retain their configured
pair and no-override rule. Model/tool allowlists remain ceilings; do not change roles or settings to
bypass the assignment. For every role, verify the resolved model and effort before delegating and
stop on mismatch rather than proceeding with a fallback. `pi-subagents` tools are excluded from codemode: the parent invokes native subagent tools,
while children use `contact_supervisor`. Do not infer availability from generic codemode prose,
retry a failing `tools.subagent_supervisor` call, or substitute a parent-only tool in a child.
Preserve configured roles and allowlists.

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
A fork does not make inherited history compact; reserve history-dependent advisory forks for existing
role policy and do not change scout defaults, settings, or configured model/effort to work around
context. Workers validate approved direction against assigned code and retain ordinary
implementation, test, and diagnostic autonomy; they do not select backlog or reconstruct full parent
history. Escalate unapproved material decisions or a concrete distinct discovery need through
`contact_supervisor` so the parent can decide whether to research or commission read-only work. Do
not widen a live assignment through unrelated investigation; the parent adjudicates genuine package
gaps and sends concise amended direction or defers distinct work. Workers do not create nested agents
or acquire new permissions. Documenting `planner` does not install or enable it; verify runtime
capability before launch. The parent retains scope, package selection, orchestration, and approvals
and launches the planner only for concrete planning/sequencing gaps—not routine or mandatory full-backlog
planning—with explicit fresh context and a compact verified-state/contracts/dependencies/approved-
decisions brief, never inherited history.

Oracle's fork is intentional and its shell access is read-only. For repository/product questions,
normative contracts outrank inherited assistant plans/history; code shows actual behavior, not intended
contract, so report discrepancies. Use only the minimum roles needed: researcher for external facts;
oracle before unresolved material architecture/safety/concurrency/migration/recovery decisions. For
unsafe boundaries, establish failure semantics and whether a decision remains; reuse a challenged
direction only after confirming its assumptions. Use `delegate` only for bounded work that fits no
specialist. Do not create recursive fan-out. The parent uses `council-mode` only when multiple
perspectives are necessary, not for routine review. Attach skills through the native harness with
`inheritSkills: false` by default; other specialist skills must be needed and role-appropriate. Browser
testers use only their explicit project skill, not unrelated skills. Do not use `/parallel-review ...
autofix`; all repository fixes go through the worker.

### Independent review

Before approval, the reviewer independently reads each obligation/dependency, acceptance criteria,
applicable product contract, and current implementation/tests/evidence at the candidate. Acceptance
and closure review covers the whole assigned obligations and dependencies, not only bugs introduced
by the diff; an ordinary diff-only regression review may focus on introduced issues but cannot
substitute for that review. Reviewing only the diff is insufficient. Check positive and failure
semantics, tests at the prior defect boundary, authorized closure, architecture/product invariants,
and applicable migration, generated contract, persistence, restart, concurrency, security, and
recovery consequences. Reviewer is read-only and does not run tests; report concrete findings and the
smallest fix. Material correctness, safety, acceptance, security, migration, or architecture findings
block approval regardless of P/risk label. `OK with notes` is acceptable only for demonstrably
non-blocking notes.

### UI/UX and browser acceptance

For browser-visible UI/UX implementation or material review, attach `ui-ux-pro-max` to the worker
and reviewer, and to the browser tester when needed. Use its search/design workflow for applicable
patterns, accessibility, anti-patterns, and stack guidance. Read the authorized acceptance and
product contract; adapt the existing design language; accessibility and repository contracts
prevail. Do not introduce a new visual system, component library, or interaction paradigm merely
from generic advice. Use `browser-tester` for changed user journeys, routing, browser state,
interactions, responsive behavior, or file flow, and for unchanged browser behavior claimed
satisfied when existing evidence is inadequate. It must exercise the browser and report flow,
expected/observed behavior, console/runtime errors, failed requests, reproduction, and PASS/FAIL.
Browser PASS does not replace other gates.

## Verification, evidence, and closure

Run focused checks while repairing. Development establishes the defect at its prior failure boundary
and runs focused tests for changes. At an internal batch freeze, integrate the assigned work and verify
its acceptance criteria, dependencies, and checks invalidated by the shared boundary; obtain the
independent review above. Independent batches may accumulate before a milestone. Before any delivery,
before dependent work treats a result as a resolved prerequisite, and at final closure, create a
milestone candidate: freeze code, tests, and ledger; run all applicable local gates; then satisfy the
exact-SHA CI requirements under Candidate gates and CI. Until the milestone gates pass, batch evidence
is internally verified only, not a finding closure or delivery. Final closure also requires cumulative
readiness and integration review on the final candidate. Frontend gates apply when `frontend/` is
touched; E2E applies when a user journey, router, browser state, interaction, or file flow changes.
Documentation-only deltas need focused diff/consistency checks, not full backend, browser, or runtime
suites solely because prose changed.

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

Passing generic gates alone does not close obligations. Map each material criterion to evidence
anchored to revision/content and command/result; include environment/image and reviewer/browser scope
as relevant. Evidence may be focused regressions, integration/browser runs, disposable fixtures,
migration/recovery/restart checks, exact-image or generated-contract proof, command output, or direct
structural evidence. Existing checks may support multiple criteria; do not duplicate tests solely by
item. Applicability-audited final checks on identical content and environment may satisfy frozen-
candidate gates. After a change, invalidate and rerun only affected checks; reuse unchanged evidence
only after explicit content/environment applicability checks. Unknown impact requires checking.
Stale results or older CI cannot substitute for current results; the latest exact-SHA CI is required.
Do not summarize evidence merely as “consistent,” “green,” or “all tests pass.”

For ledger closure, the parent approves and the designated integration worker edits the active ledger,
preserving its retention policy. Consolidate batch evidence at integrated freeze and milestone in a
shared evidence section with criterion references, not duplicated logs. Before a milestone, record
results as internally verified and keep finding statuses open; do not call them closed or delivered.
Closure requires current behavior, adequate criterion-mapped proof, independent review, and applicable
local gates. Preserve history where retained; remove completed rows only when the ledger is
unfinished-work-only. Keep unsatisfied criteria open; proposed behavior, a touched file, doc edit, or
dependent test alone is not closure or prerequisite resolution. Map shared fixes to every closed
obligation without duplicating tests/reviews. Track out-of-scope work only with parent approval.

## Autonomous goal delivery

A whole-backlog request is one goal; packages are checkpoints, not separate goals. Do not narrow
whole-goal success to the first package. At goal start, establish initially open obligations, scope,
dependencies, delivery mode, artifact locations, and pre-existing worktree/index state. Do not
require the owner to select the next authorized package or invent work for an absent/completed
obligation.

### Candidate gates and CI

A milestone is required before any delivery, before dependent work consumes a result as a resolved
prerequisite, and at final closure. Independent batches may accumulate before a milestone, but are
internal verification only and are not findings closed or delivered. Do not start dependent work until
the prerequisite evidence and exact-SHA CI required below have passed.

- Freeze code, tests, and ledger before final gates. Apply the applicability, documentation-only,
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
  Do not ask the owner to waive or reorder local gates to work around an environment failure.
- `no push` and `no commit` suppress only those delivery actions, not exact-SHA CI. If exact accepted
  content cannot be tested under that mode, the goal remains incomplete pending owner decision.
- Full-backlog completion requires every applicable readiness gate and cumulative integration review
  on one final candidate/image, as specified in Verification, evidence, and closure and
  `docs/production-readiness.md`; package closure or CI alone is insufficient.

### Execution and completion

Before implementation, assess backlog/dependencies and current code/tests/evidence, record residuals,
verify prerequisites, then select an authorized package. Do not rewrite adequate behavior to
demonstrate activity. Follow Package lifecycle and writer, Independent review, and UI/UX and browser
acceptance; the parent adjudicates findings and the worker fixes accepted blockers. Apply Scope,
assessment, and packaging's reassessment, evidence-mapping, and ledger rules after each package.

The parent finalizes accepted content only. Exclude unrelated changes; if hooks mutate tracked
content, return it to the worker and invalidate affected review/checks. Do not make an empty commit
when accepted content is already exactly at HEAD or rewrite history. For submission, independent CI work,
and final delivery, follow Candidate gates and CI; verify the exact-SHA result before claiming
delivery or starting dependent work. If blocked, preserve state and assess independent authorized
work without mixing candidates. Complete only after all authorized obligations and applicable gates,
review, browser, CI, and delivery requirements pass; call `goal_complete` with its exact current ID.

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
  extra calls, plans, status polls, or identical runs are not progress. Two consecutive repair/recheck
  cycles on the same residual without substantive progress trigger one bounded diagnosis. Continue
  only with a new testable direction/evidence; otherwise suspend the package, preserve state, and
  assess independent authorized work. Do not ignore productive work or a concrete blocker to meet an
  iteration target.
- Distinguish complete, waiting, structurally blocked, no-progress, resource-limited, external-error,
  and owner-decision states. Completion requires proof. `goal_wait` needs an arranged external
  wake/deadline; `goal_blocked` needs a genuine evidenced external impasse and its required repeated
  turns. Never manufacture failed attempts or repeated turns, invoke unavailable lifecycle controls,
  or claim prose enforces runtime termination.
- Ordinary waiting for active workers/reviewers, tests/builds, or CI is part of goal execution: keep
  the goal active, use native completion wakes and supported quiet waits, and resume authorized work
  automatically. When awaiting external CI, arrange a supported monitor/wake rather than polling.
  Do not pause the goal to suppress checkpoint loops, notification spam,
  idle parent turns, or routine waiting overhead. Waiting for progressing delegated work is not a
  no-progress repair cycle. Diagnose an actual harness waiting limitation separately; do not bypass
  permissions or treat waiting itself as paused or blocked. Owner-requested pauses and genuine safety,
  permission, resource, quota, budget, deadline, or decision blockers still follow the stopping rules
  below.
- Respect configured resource limits; do not renew grants, remove caps, or switch protocols without
  authority. Record whether accounting covers parent/children; unknown child usage is not zero.
  Before a known budget/deadline boundary, checkpoint at a safe tool boundary and launch nothing
  unable to finish safely. Use native retry/backoff for transient errors; identify failures and
  preserve partial state before retrying. Never blindly rerun a package or treat infrastructure
  failure as product failure; reattach to the same job/run.
- If safe pause is unavailable, first exhaust safe capabilities within scope and permissions, then
  checkpoint and launch no further agents on the stalled package. Request owner intervention only
  for concrete missing authorization or a product decision; otherwise report a genuine externally
  unresolvable safety blocker. Do not turn routine diagnostics or lifecycle recovery into manual
  chores. Never weaken acceptance, bypass dependencies, contact live providers, alter user-owned
  data, or silently widen scope.
- Keep one compact runtime checkpoint in existing session/mission storage outside repository
  content; use supported host persistence and do not assume an unavailable tool. Update it on
  significant changes, handoffs, interruptions, and resource limits, not on every call. Do not create
  another backlog or permanent recovery report. At handoffs/interruption retain scope/delivery mode,
  backlog, HEAD/candidate, pre-existing and owned paths, active package/residuals/prerequisites/
  decisions, worker/session/run IDs and live status, reviewer/browser blockers and last hypothesis,
  commands/environment, evidence paths, resource-coverage state, commit/push/upstream/CI, and stopping
  reason. Preserve acceptance-critical evidence in existing durable storage as soon as it is produced,
  including snapshots, diffs, and review outputs; do not keep it only in auto-pruned output or temp
  paths. Do not add repository reports, another backlog, or a process framework.
- On resume, inspect HEAD, diff, ledger, live writers, and the exact pending CI/job; reconcile receipts
  and invalidate incompatible evidence. Reuse only applicable evidence. Do not replace a writer
  until it is stopped and ownership is certain; do not redo delivered packages blindly.

## Handoff

Report authorized outcome, selected package/obligations, behavior changed, files, criterion-mapped
evidence, commands/results, reviewer and browser/E2E verdicts when applicable, migration/generated/
runtime impact, residual risk/blocker, and next authorized step or explicit stopping reason. Do not
claim goal completion without all required evidence and delivery gates.
