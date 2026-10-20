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
- Finding IDs and criteria are acceptance/reporting units; packages are implementation/delivery units.
  Explicit-ID goals target only the named findings: never drop or add IDs. By default handle one
  finding; group two or three only when a shared cause, contract, implementation, or acceptance makes
  one coherent package. A request of up to three IDs may form at most two coherent packages; if all
  requested IDs cannot fit those bounds without arbitrary grouping, ask the owner rather than silently
  narrowing or widening scope. More than three IDs or two packages requires owner authorization.
- For a generic `/goal Risolvi i prossimi finding`, autonomously assess current open/reopened residuals,
  current evidence, actual dependencies, risk, and package coherence, then select up to two packages
  and three finding IDs total. Default to one finding; do not fill the cap merely to use it. No routine
  owner confirmation is needed. Freeze every selected ID, package boundary, criterion, dependency,
  endpoint, and criterion-to-evidence/residual map before implementation in the existing brief/session
  storage; do not create another planning artifact. Replan only for material evidence, and get owner
  authorization before adding IDs or changing frozen package scope. Never refill or reselect after a
  package closes or blocks.
- The parent owns current-evidence assessment, package/residual selection, approved direction/failure
  semantics, gate applicability, and closure approval. Record initial scope/worktree state and map
  each criterion to evidence or a concrete residual at an identified revision. Treat old diagnoses
  and fixes as leads. If behavior/evidence suffice, do not rewrite; if only proof is missing, verify
  or add only needed regression evidence; otherwise fix the residual. Generic green suites or touched
  files alone are not proof.
- Preserve explicit and phase prerequisites; do not infer order from IDs or silently absorb an
  out-of-scope prerequisite. Generic selection may include a prerequisite before scope freeze, within
  the same caps. After freeze, an undelivered prerequisite may be consumed only when it is included in
  the same coherent package and its complete criteria plus consumer/provider integration are proved
  under Verification, evidence, and closure. Otherwise an unsatisfied out-of-scope prerequisite blocks
  dependent work; report it and do not open or implement another package without authorization.
  Necessary technical work for assigned criteria is not automatically a separate finding. Select by
  residual need, risk, shared-boundary leverage, satisfied prerequisites, then locality/context and
  verification cost; invent no numeric estimates or optimize only for fewer calls. Do not overbundle
  unrelated findings or split shared fixes by ID. The optional read-only planner is advisory, not an
  automated planner/workflow; require a demonstrated capability gap to add such capability. Keep the
  selected plan unless material evidence changes it; consult advisers only for a concrete gap. Use the
  sequential default; concurrent lanes remain subject to Package lifecycle and writer.
- Default to one coherent package through implementation, acceptance, applicable gates, exact-SHA
  CI, authorized delivery, and closure. A second package is permitted only if it was included in the
  frozen bounded batch before implementation and is independently coherent; parallel lanes additionally
  require all existing semantic, resource, model, worktree, and single-writer safeguards. Otherwise
  proceed sequentially. Each package has separate acceptance and delivery evidence; never mix evidence.
  No newly selected package may start while delivery CI is pending. A previously frozen independent
  package may progress only under the authorized batch and existing isolation rules. Blockage or
  closure never authorizes replacement scope; finish only the frozen goal, report each package/finding
  outcome, and stop. Record package criteria, integration/failure proofs, review/browser scope,
  gates, and delivery exit in the existing worker brief. Routine review, tests, setup, or CI waiting
  are not blockers or permission to expand the batch.
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
  known-broken state or push it. An explicit `no commit` forbids checkpoints; every goal that changes
  repository content still requires a final local commit and direct normal fast-forward push of all
  accepted changes to canonical `vertillo/muzilla` `refs/heads/main` for successful completion. A
  `no commit` or `no push` restriction cannot waive that delivery requirement. A PR merge workflow
  does not substitute for the direct push. Only the parent may push, after verifying the repository
  identity and target branch. Before pushing, verify the isolated delivery worktree has no unresolved
  merge/index conflict entries; this does not require cleaning unrelated changes in other worktrees.
  After pushing, verify the remote `refs/heads/main` contains the accepted commit. If the configured
  upstream is not the intended repository's `main`, or a push is non-fast-forward, stop and ask rather
  than bypassing the issue; do not force-push, choose another remote, change remote/branch/config, or
  automatically rebase, merge, or reconcile. Never rewrite, squash, amend, rebase, reset, or otherwise
  alter user-authored history without authorization. This does not authorize permission or secret
  changes, tags, releases, publication, or deployment. Preserve and report a blocked push.

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
  checks, package delivery exit, and stop/escalation conditions. Distinct filenames alone do not
  establish independence.
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
  distinguishing blockers from notes. Re-review the repair delta, prior blockers, and affected
  criteria—not the whole package from scratch. Completed checks and review remain applicable until
  relevant content, contracts, assumptions, or environment changes invalidate them. A material
  direction/boundary change requires review adequate to that change; unrelated discoveries do not
  justify full rediscovery. Aim
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
smallest fix. An unsatisfied original criterion, a regression introduced by the fix, or a blocker in
the package's affected shared boundary blocks acceptance and must be repaired. An independent
pre-existing issue does not automatically expand the package or its authorized finding target; report
it separately, and include additional work only with parent approval. Always disclose critical release
blockers and preserve existing safety holds; never weaken acceptance or suppress a defect to preserve
scope. `OK with notes` is permitted only for demonstrably non-blocking issues.

### UI/UX and browser acceptance

For browser-visible UI/UX implementation or material review, attach `ui-ux-pro-max` to the worker
and reviewer, and to the browser tester when needed. Use its search/design workflow for applicable
patterns, accessibility, anti-patterns, and stack guidance. Read the authorized acceptance and
product contract; adapt the existing design language; accessibility and repository contracts
prevail. Do not introduce a new visual system, component library, or interaction paradigm merely
from generic advice. Use `browser-tester` only for browser behavior affected by the package or an
explicit browser acceptance criterion without adequate evidence. Exercise the affected journey,
interaction, routing/state, responsive/accessibility behavior, or file flow—not unrelated journeys.
A backend-only change requires browser acceptance only when its changed contract can affect browser
behavior or the finding explicitly requires it. Reuse applicable existing evidence. Report the tested
flow, expected/observed behavior, console/runtime errors, failed requests, reproduction, and PASS/FAIL.
Browser PASS does not replace targeted failure proofs or milestone gates.

## Verification, evidence, and closure

Use these canonical outcomes for each frozen package/finding; they do not alter ledger status by
prose alone. “Implemented” is progress, not a terminal outcome.

- **CLOSED:** every frozen finding satisfies its criteria and dependencies with mapped evidence,
  independent review, applicable full local gates, latest successful exact-SHA CI for all required
  jobs, authorized delivery, and a delivery receipt. A batch is CLOSED only when every selected
  finding is CLOSED.
- **INTERNALLY_VERIFIED:** every technical criterion and prerequisite is satisfied at identified
  content, with positive/negative and applicable integration/failure evidence, independent review,
  and parent acceptance; delivery remains outstanding. This is a successful terminal endpoint only
  when the owner explicitly authorized an internal-only task before work began and the goal produces
  no repository changes. For any repository-changing goal, it is progress only until delivery to
  canonical `vertillo/muzilla` `refs/heads/main`; if delivery cannot proceed after safe authorized
  alternatives, report BLOCKED and retain the internal proof. `no push`/`no commit` do not waive this
  requirement.
- **BLOCKED:** a concrete acceptance or delivery impediment remains after safe authorized alternatives
  are exhausted. Record the blocked criterion/step, evidence, and resumption condition; internal proof
  may be reported but does not turn a delivery blocker into success.
- **NEEDS_OWNER_DECISION:** a material scope, product, security, permission, or endpoint decision is
  required before safe continuation.

Establish the defect at its prior failure boundary, run focused regressions during repair, and check
integrations affected by the changed boundary. Before INTERNALLY_VERIFIED, satisfy every criterion and
obtain the independent review above. Proceed directly to delivery by default. A second independent
package may proceed only if already frozen in the same bounded goal; parallel work requires a separately
isolated lane under existing writer rules, otherwise complete packages sequentially. Run full applicable
local suites and exact-SHA CI on each frozen package's delivery candidate, not after each finding or
repair. Reuse evidence only when identified content and environment remain applicable.

Within one explicitly bounded coherent delivery package, dependent implementation may consume an
undelivered prerequisite before milestone CI only when shared contracts or acceptance justify grouping.
Verify every prerequisite criterion at identified content and the consumer/provider integration,
including applicable concurrency, persistence, restart, migration, recovery, and security failure
semantics. Preserve actual finding and phase dependencies; a dependent test alone cannot prove its
prerequisite. Explicit full-workflow or exact-image acceptance remains mandatory. No per-ID CI is
required within this package. Otherwise, the prerequisite must already be delivered or an unsatisfied
out-of-scope dependency blocks the dependent work.

Domain outcomes are not harness lifecycle controls. Use `goal_complete` only when the active tool
contract permits it and all evidence for the authorized endpoint is verified; BLOCKED does not
authorize claiming completion. Do not invent pause/cancel capabilities or manufacture a repeated-turn
precondition.

At delivery, freeze code, tests, and ledger and run the applicable full local gates below, then satisfy
Candidate gates and CI. Frontend full gates apply when frontend changes are included; applicable E2E
gates cover changed journeys and explicit acceptance obligations. Documentation-only deltas require
focused diff/consistency checks, not artificial backend, browser, or runtime suites. Full production-
readiness gates govern production readiness and final whole-backlog completion, or a finding that
explicitly requires them; they are not blanket prerequisites for unrelated finding closure. Explicit
finding-specific performance, exact-image, recovery, and other criteria remain mandatory.

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
- Apply relevant recovery/restart, backup/restore, reset, scale/performance, secret-handling,
  dependency/runtime-audit, and exact-image gates from `docs/production-readiness.md` when required
  by package boundaries or finding criteria. Final whole-backlog completion and production readiness
  require cumulative applicable gates on the final candidate/image.

Passing generic gates alone does not close obligations. Map each material criterion to evidence
anchored to revision/content and command/result; include environment/image and reviewer/browser scope
as relevant. Evidence may be focused regressions, integration/browser runs, disposable fixtures,
migration/recovery/restart checks, exact-image or generated-contract proof, command output, or direct
structural evidence. Existing checks may support multiple criteria; do not duplicate tests solely by
item. Applicability-audited final checks on identical content and environment may satisfy frozen-
candidate gates. After a change, invalidate and rerun only affected checks; reuse unchanged evidence
only after explicit content/environment applicability checks. Unknown impact requires checking.
Stale evidence cannot substitute for applicable current proof. Delivery requires the latest successful
CI for the exact milestone SHA; package technical acceptance does not require separate hosted CI.
Do not summarize evidence merely as “consistent,” “green,” or “all tests pass.”

The parent approves internal acceptance and delivery; the designated integration worker updates the
existing ledger and preserves its retention policy. Record implemented findings with residual criteria,
INTERNALLY_VERIFIED findings with criterion-mapped evidence/review, and CLOSED findings with milestone
SHA and delivery receipt. Pending milestone gates are delivery residuals, not missing technical criteria.
Never describe implemented or internally verified work as delivered or unqualifiedly closed. A blocked
batch remains blocked even when one package is closed; do not select replacement findings. Consolidate
shared evidence rather than duplicate logs. Preserve original diagnoses and historical receipts. Keep
unsatisfied criteria explicit; proposed behavior, touched files, documentation edits, generic green
suites, or a dependent test alone do not establish acceptance. Reassess affected obligations when
shared boundaries change. Track unrelated work only with parent approval.

## Autonomous goal delivery

Every finding-remediation goal has a finite scope frozen before implementation. For an explicit-ID
goal, target exactly those IDs. For `/goal Risolvi i prossimi finding`, assess current residuals and
select up to two coherent packages and three finding IDs total; default to one finding and do not
refill a closed or blocked slot. More than two packages or three IDs requires owner authorization.
No routine confirmation is needed for an in-cap generic selection. Record the frozen IDs, package
boundaries, criteria, dependencies, delivery endpoint, and evidence map in existing brief/session
storage; do not create another plan. A material evidence change can justify a proposed replan, but
adding IDs or changing frozen boundaries requires owner authorization.

Examples: `/goal Implementa R15` targets R15 only. `/goal Implementa R19 e R20` targets those two
IDs only; group them as one package if current evidence confirms their shared cache contract. Do not
silently add prerequisites such as R17/R18 or unrelated findings; an unsatisfied out-of-scope
prerequisite blocks dependent work. A generic request may select a prerequisite before freeze when
it fits the same caps. Once the frozen scope reaches its authorized endpoint, report it and stop; do
not continue as an open-ended backlog pipeline.

### Candidate gates and CI

Each coherent package is one implementation-to-delivery unit. Complete its acceptance, applicable
milestone gates, latest successful exact-SHA CI, and authorized delivery/closure before selecting any
new package. A second independent package may proceed only if already frozen in the same bounded goal;
parallel execution requires the isolation rules in Scope, assessment, and packaging, otherwise process
packages sequentially. Internal acceptance is not delivery, and work may not accumulate outside the
frozen batch. The only pre-CI dependency-consumption exception is within the same coherent delivery
package under Verification, evidence, and closure.

- Freeze code, tests, and ledger before final gates. Apply the applicability, documentation-only,
  evidence-reuse, independent-review, and browser-acceptance rules in Verification, evidence, and
  closure to that frozen candidate.
- Only the parent may submit by normal fast-forward push to the configured upstream. The latest
  workflow run for the exact 40-character final commit SHA must have every required job successful;
  none may be pending, failed, missing, or skipped. Record run ID, URL, SHA, and job results. Local
  checks or older CI do not substitute.
- Pending CI is a delivery blocker, not a window to select or add a package. The only work that may
  continue is another independent package already frozen in the same bounded batch, and only under
  the existing isolated-lane rules. Routine CI waiting is part of the goal, not permission to expand
  scope or claim delivery.
- Every later commit submitted as a milestone delivery candidate requires its own latest green
  exact-SHA CI and applicable local-gate evidence. Review the delta and affected acceptance; do not
  repeat full review solely because SHA changed.
  Record the verified implementation/content anchor and CI receipt without an extra commit that
  invalidates CI. Never weaken gates or permissions to repair a failure.
  Do not ask the owner to waive or reorder local gates to work around an environment failure.
- `no push` and `no commit` do not waive exact-SHA CI or required integration to canonical
  `vertillo/muzilla` `refs/heads/main`. A goal producing repository changes cannot be complete without
  that direct integration; if the requested restrictions prevent delivery, report it incomplete or
  BLOCKED rather than treating a skip as completion. If exact accepted content cannot be tested under
  that mode, the goal remains incomplete pending owner decision.
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
when accepted content is already exactly at HEAD or rewrite history. For submission and final delivery,
follow Candidate gates and CI; verify the exact-SHA result before claiming delivery. Consume an
undelivered prerequisite only within the same coherent delivery package under the technical dependency
rule above. After acceptance, proceed to delivery; only another package already frozen in the same
bounded batch may continue under the rules above. A genuine external blocker may suspend the affected
package, but does not authorize replacement scope; preserve its evidence and resumption condition.
Finish and report only the frozen scope, then stop. Call `goal_complete` only when the active tool
contract and the authorized endpoint's evidence permit it; a domain BLOCKED outcome is not harness
completion.

`goal_complete` evidence includes: authorized outcome and every obligation/criterion map; changed
files; delivery mode; final accepted commit SHA (or current HEAD plus explicit no-commit owner override
only when no repository changes were made); for repository-changing goals, canonical repository
identity, observed remote `refs/heads/main` SHA, direct push result, and proof that the accepted commit
is integrated on that branch. An authorized push skip is acceptable only when no repository changes
were made. Include exact commands/results; latest exact-SHA CI run ID/URL/SHA/jobs; reviewer and
browser results; generated-contract, migration/database, and image/runtime impact; residual risk.
Large goals may cite verified durable per-obligation audits and delivery receipts instead of copying
logs. References must point to evidence, not substitute for it. Where no commit is made, gates apply
to exact accepted uncommitted content, but this cannot satisfy completion for a repository-changing
goal; any later commit-time content change invalidates affected checks.

## Convergence, stopping, and resume

- At each handoff identify the smallest remaining acceptance or delivery action and the evidence
  it must produce. Progress means a criterion proved, an accepted blocker repaired, or a package
  delivered—not calls, setup, test volume, or agent rotation. Two consecutive repair/recheck cycles
  on the same residual without substantive progress require one bounded diagnosis. Continue only with
  a new testable direction; otherwise explicitly suspend the package with its blocker and resumption
  condition. Never close broken work to satisfy an iteration limit, repeat setup, or rotate agents as
  a substitute for diagnosis.
- Repair package blockers at the smallest affected boundary, then recheck the delta, prior blockers,
  and affected criteria; reuse review and checks that remain applicable. Additional repair needs a
  concrete remaining criterion or new testable diagnosis. Record infrastructure failures with the
  blocked proof and next discriminating check. Build only the fixture or harness needed for that proof;
  harness complexity is not product progress. If instrumentation itself is the assigned finding, its
  correctness and required real-workflow evidence remain acceptance obligations. Never substitute
  infrastructure work for user-visible or safety proof.
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

Report newly closed/delivered finding IDs first with milestone SHA and CI/delivery receipt, or state
“no new closures.” Then report implemented or internally accepted progress with its remaining criteria.
Name the current package, exact blocker if any, and smallest next acceptance or delivery action.
Preserve criterion evidence and applicable reviewer, browser/E2E, migration, generated-contract, and
runtime results. Do not count internal acceptance as closure or delivery, or claim readiness/goal
completion without its required evidence.
