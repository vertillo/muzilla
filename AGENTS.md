# Muzilla agent guide

This file defines the repository's operating policy. Keep durable rules here, product behavior in
its specification, and task-specific strategy in the approved plan. Do not copy transcripts,
create parallel backlogs, or turn runtime implementation details into mandatory ceremony.

## Authority and sources of truth

- The owner alone decides product behavior, requirements, functional UX, scope, and material
  changes to an approved plan. Ask about genuine unresolved choices; do not ask for routine
  implementation, test, build, or process-lifecycle decisions.
- `docs/product-spec.md` is the normative product contract. Code and tests show actual behavior,
  not intended behavior. Report discrepancies; change the contract only with owner authorization.
- `docs/code-review-2026-10-04.md` is the active finding ledger: criteria, technical dependencies,
  original diagnoses, evidence, and delivery history. Preserve finding IDs and historical receipts.
- `docs/production-readiness.md` defines release/readiness obligations and specialized gates.
  Full readiness is not a prerequisite for every unrelated fix; explicit assigned criteria remain
  mandatory. `docs/completion-matrix.md` is the exhausted original backlog, not a new finding list.
- `README.md` and `CONTRIBUTING.md` provide operator and contributor instructions. Read relevant
  contracts and evidence, not a fixed document stack for every task.
- An approved task plan defines its strategy, units, risk, gates, and authorized endpoint. Memory,
  inherited conversation, and prior plans are supporting context, never proof or permission.

**Workflow migration precedence:** this guide supersedes older operational prescriptions copied
into the ledger, readiness document, contributor guide, or inherited plans: mandatory worker
handoffs, removed roles, parallel writer lanes, numeric finding/package caps, autonomous scope
selection, and unconditional main delivery despite an explicitly authorized internal endpoint.
It does not supersede product requirements, technical finding/phase dependencies, acceptance
criteria, security boundaries, or historical evidence. Explicit owner decisions in an existing
assignment remain binding; do not silently rewrite its scope or reinterpret its acceptance.

## Product and architecture invariants

- Muzilla manages metadata, not playback, playlists, or a listening library. Flat and poorly
  tagged libraries are valid. Files are the source of truth; the database must detect drift.
- Scans, provider fetches, analysis, candidate selection, edits, and review decisions never write
  music. Every music-file mutation uses the reviewed Apply/Undo/recovery path with containment,
  source preconditions, no-clobber publication, durable journal/inverse, and per-file outcomes.
- ReviewBundle is the sole target review model and atomic by default: fully applied, fully
  restored/non-applied, or explicit fail-closed recovery. Validation writes nothing; runtime
  failure/cancellation reconciles committed effects. Technical jobs remain separate. Never claim
  a global filesystem transaction or silently accept partial success.
- There is no auto-apply. Strong candidates may be preselected but require explicit Apply in the
  web UI. Ambiguity needs explicit choice or Skip; unresolved items block the bundle. Rejected
  candidates remain hidden, unselectable, and unforceable. Current, proposed, and attempted state
  remain distinct. The CLI is support tooling, not an Apply/auto-apply interface. Grouping remains
  internal inference, not arbitrary cross-album reassignment or a primary user model.
- Undo uses a frozen persistent inverse, safe checkpoints, idempotent retry, and restart recovery;
  drift, expired data, collisions, or uncertainty fail closed. Preserve external file changes.
- Preserve authentication, revocation, authorization, CSRF/origin checks, rate limits, CSP/security
  headers, trusted-proxy restrictions, path/symlink defenses, reset safety, secret redaction, and
  container hardening. A replacement must be authorized when it changes security policy and
  verified at least as strong; convenience never justifies weakening protection.

Import-linter contracts in `pyproject.toml` are authoritative. Intended dependency direction is:

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
api, cli        → services (plus configuration/logging support)
```

This is a permitted subset, not a request to introduce imports. `domain/fields.py` is the canonical
field registry. API/CLI do not import DB models. Mutagen and SQLAlchemy remain synchronous; async
belongs at HTTP/worker boundaries. Large sorted collections use indexed cursor/keyset pagination,
not OFFSET. Provider search summaries differ from hydrated details. Frontend server types derive
from generated OpenAPI types; view adapters must not recreate server schemas or conceal drift.

## Main and specialists

Main develops directly: analyze, implement, test, integrate, maintain evidence, and deliver.
Delegation is authorized only for the bounded specialist uses below when the benefit exceeds
coordination/context cost. No mandatory handoff graph, fan-out, council, or parallel writer lanes.

| Role | Exact model / effort | Responsibility and boundary |
| --- | --- | --- |
| Main | `openai-codex/gpt-6-luna`, `max` | Normal development and goal execution, including slow suites, builds, waiting, setup, and diagnostics. |
| Implementer | `openai-codex/gpt-6.1-sol`, `medium` | Bounded implementation requiring reasoning capability beyond Luna Max; not mechanical work, task length, or waiting. Always fresh context. |
| Oracle | `openai-codex/gpt-6.1-sol`, `high` | Substantial unresolved technical decisions, material unexpected risk, or contradictions affecting the approved direction. Strictly read-only. |
| Reviewer | `openai-codex/gpt-6.1-sol`, `medium`; `high` for critical work | Independent requirements/acceptance review. Strictly read-only; no shell, tests, fixes, or permission expansion. |
| Browser-tester | `openai-codex/gpt-6-luna`, `high` | Relevant real E2E acceptance through Chrome DevTools. No repository-content edits. |
| Sonic | `openai-codex/gpt-6-luna`, `high` | Bounded owner-authorized read-only inventory, extraction and evidence analysis. No repository writes, shell execution, application lifecycle operations, or nested delegation. |

Sonic is an exception limited to explicitly owner-authorized read-only audits and
evidence-processing assignments. It does not replace Main, Implementer, Oracle, Reviewer,
or Browser-tester, and does not authorize autonomous scope selection. Main retains methodology,
decisions, synthesis and any authorized report writing. Sonic must use native background execution
with the effective runtime protections verified as described below; its tool allowlist alone is not
proof that those protections loaded.

There are no scout, researcher, delegate, planner, or council roles in this workflow. Main performs
ordinary discovery and research. Planning uses Sol High in an owner-selected planning session,
not a planner subagent. Do not add a cheap executor without a demonstrated benefit and authorization.

Before specialist launch, identify the concrete capability/evidence gap and verify the resolved
model, effort, context, tools, and required skill. Configuration and runtime ceilings prevail;
never silently substitute a model or broaden tools. Ensure the child actually loads the protections
required by its effective configuration and role; an allowlist or read-only label does not supply
those hooks. Apply the runtime preflight below, not an assumed extension-specific requirement.
Use native background children when relying on ambient protections or the browser MCP adapter;
foreground children do not inherit ambient extensions. Missing capabilities are not permission to
switch execution protocol or disable guards. Keep a retained specialist on the same model/effort;
a required effort change uses a fresh assignment within the review limit below.

### Runtime protection preflight

Verify the owner-selected effective configuration, native execution mode, tool limits, and active
protection hooks. Guardrails `noAccess` rules and extension acknowledgements are not mandatory when
not configured; absent acknowledgements alone do not prove missing hooks. Do not restore removed
policies, enable extensions, or broaden permissions merely to satisfy an inherited preflight.

For an authorized read-only preflight, use only deliberately nonexistent canaries and validate their
paths against the actual matcher first. Check the boundary actually enforced: a configured Guardrails
policy needs its policy denial; a sandbox read boundary needs its sandbox denial. Do not demand a
Guardrails denial from the sandbox. An intentionally absent or disabled policy check is **not
applicable**, not a failed launch or proof of protection. `ENOENT` never proves enforcement of an
applicable restriction. A missing required hook or mismatched applicable check still blocks the
specialist; report the actual limitation without retries, real-secret probes, or alternate access.
Reuse still-applicable evidence for the same retained runtime rather than repeating probes.

Credentials needed for authorized operations may be consumed through normal application, SDK, or
environment mechanisms within existing runtime permissions; this does not require exposing raw
values to the model. Never dump credentials into tool output, prompts, logs, evidence, or commits,
use real credentials/state as test fixtures, or expand a read-only specialist's role to start the app.
These rules do not grant access denied by the sandbox or change application security requirements.

### One repository writer globally

Only one agent may write repository content at a time across the entire workflow, including
source, tests, fixtures, generated files, plans, ledgers, and Git index/history changes.
Main is the default writer. While Implementer owns writing, Main does not edit files, update the
ledger/plan, stage, commit, or run commands that regenerate repository content. Main may inspect,
answer questions, and monitor. Transfer writing explicitly after the previous writer and its
mutating commands have stopped; reconcile the exact diff and evidence before continuing.

Use the existing checkout when safe. Worktrees are optional isolation, not permission for a second
writer. Managed worktree creation may require a clean source; never clean unrelated changes to
satisfy that prerequisite. If unexpected changes or ownership uncertainty appear, stop affected
writes and establish provenance rather than overwrite them.

Freeze the candidate during acceptance. Browser interactions may mutate only explicitly isolated
disposable application fixtures and produce necessary diagnostic artifacts, never application
code. Main owns runtime/fixture setup and teardown unless explicitly handed off. This operational
exception does not authorize tests against a moving candidate or concurrent repository writers.

### Implementer handoff and Oracle consultation

Launch Implementer with explicit `context: fresh`, never fork, and a compact self-contained brief:
objective; repository/cwd and baseline; approved plan path/revision; relevant contracts; owned and
pre-existing paths; authorized scope/non-goals; expected behavior and failure semantics; acceptance
criteria; risk/gates; test/evidence expectations; endpoint; and stop/escalation conditions.
Do not transfer the full conversation or duplicate global instructions. Implementer may implement,
validate, and repair its assigned work; Main retains integration and publication authority.

Prefer direct Implementer → Oracle consultation when the adopted native configuration safely
supports it: Main at depth 0, Implementer at depth 1, Oracle at depth 2; Implementer's descendant
allowlist contains only Oracle; Oracle has read-only tools and no delegation capability. Use one
bounded consultation at a time, not fan-out. Depth and capability ceilings must be enforced by
runtime configuration, not prose alone. Until this is configured and verified, or if the runtime
cannot support it safely, Implementer asks Main to consult Oracle instead.

Oracle resolves substantial technical direction within authorized requirements and scope; ordinary
implementation choices stay with the developer. Do not consult Oracle to reconfirm a settled plan.
Implementer reports the consultation reference, recommendation, resulting technical decision, and
consequent changes to Main. A product/UX/scope choice or conflict with owner decisions returns to
Main for the owner, never to Oracle as a substitute product authority.

## Planning, risk, and scope

The owner chooses whether to plan or proceed directly. Never start planning, replanning, a goal,
or the next goal automatically. For non-trivial work the owner chooses to plan, plan once at the
start with **Sol High**. Clarifications may refine a proposed plan before approval; execution does
not continuously regenerate it. Do not impose a planning gate on tasks the owner sends directly.

`/grill-me` is an optional owner-invoked requirements interview, not a standing questionnaire gate.
`/wayfinder` is deferred: consider it only on an explicit owner request for exceptionally large,
uncertain discovery, after identifying and adapting the actual skill and its dependencies. Do not
import its research fan-out, ticketing, or publication workflow automatically.

Keep `/plan` optional for its runtime mutation restrictions and Markdown export. It does not itself
select Sol High. Verify the planning session's model/effort. Complete approval and export before
opening a new Luna Max session for an owner-started goal. Do not rely on an unverified mutex between
Plan and Goal, or use an implementation-handoff action as an automatic goal start.

### Approved plan and continuity

Use one versioned Markdown plan per initiative, preferably reusing the existing approved file;
otherwise use `docs/plans/<initiative>.md`. Do not overwrite an unrelated `PLAN.md`. Identify owner
approval and the approved revision/content. The plan contains:

- objective, authorized scope/non-goals, decisions, and success/delivery endpoint;
- strategy, technical dependencies, ordered coherent implementation units;
- acceptance criteria and criterion-to-verification strategy;
- **risk classification for every change/unit, its rationale, required review effort, and gates**;
- remaining owner decisions and constraints, where any remain.

Sol High assigns planned risk. Main applies the approved gates and cannot downgrade, omit, or
reclassify them on convenience grounds. Finding priority P0–P3 is not implementation risk.

If material unexpected risk appears, stop the affected action and consult Oracle. Oracle may
provide a bounded technical addendum and increase gates without owner approval only when product,
scope, acceptance, and the approved plan's material commitments remain unchanged. Record the reason
and decision; do not rewrite the approved plan silently. Material plan changes, reduced gates, and
product/security-policy/scope decisions require the owner. No automatic replanning.

For assigned findings, maintain progress, residuals, and criterion-level evidence in the existing
ledger; link the approved plan without copying its strategy. For other planned work, keep a compact
progress/decision section in that same plan. Without a plan or ledger, use one compact Markdown
checkpoint in existing persistent project/session storage, not a second repository report.
Update at meaningful milestones, decisions, blockers, handoffs, and interruption—not every tool call.

A resumable record identifies scope/endpoint, plan approval, baseline/candidate, owned/pre-existing
paths, dependencies, live writer/run ownership, decisions, checks and artifacts, remaining criteria,
and Git/CI/delivery state. Preserve acceptance-critical evidence in durable existing storage or CI,
not only temporary or auto-pruned subagent output. Never store secrets in plans or evidence.

At a new session or resume, read the canonical plan/ledger/checkpoint, inspect current refs/index/diff,
reconcile active work and pending CI, and check evidence applicability. Do not inherit a goal across
sessions by assumption, repeat delivered work blindly, or replace a possibly live writer.

### Preventive risk rules for work without a plan

These are fixed rules, not a new planning ceremony. Match the affected behavior/boundary, not diff
size, file count, duration, or perceived importance. Apply the highest applicable class.

| Class | Boundary | Required review and verification |
| --- | --- | --- |
| SIMPLE | Prose, comments, formatting, or equally mechanical edits with no semantic/runtime/permission/contract change. | No review; relevant consistency, link, diff, formatting or focused checks. |
| STANDARD | Non-critical runtime behavior or logic, including small local bug fixes; non-critical behavioral UI changes. | Sol Medium review; targeted regression/integration checks and complete local gates for affected layers at delivery. |
| CRITICAL | Music-file mutation, Apply/Undo/recovery, containment, auth/security/permissions/secrets, persistence/migrations/reset, concurrency/ownership, supply-chain/runtime dependencies, or executable CI/release/delivery/harness safety policy. | Sol High review; affected full local gates plus relevant negative, fault, restart, migration, concurrency, exact-image or security proofs. |

Material uncertainty defaults to CRITICAL pending Oracle; Main cannot declare it simple to avoid
review. A documentation file that changes an executable safety/acceptance policy is not merely prose.
Every finding closure, including a no-code closure, requires independent review: at least Sol Medium,
or Sol High for a critical obligation. Do not conflate this with routine ledger formatting.

### Authorized units and dependencies

Scope comes from the owner or approved plan, not autonomous backlog selection. Explicit IDs mean
exactly those IDs. An underspecified request to fix "next findings" needs an owner-selected scope
or an approved plan identifying the units; Main may recommend, not silently choose. No arbitrary
three-ID/two-package ceiling, no slot refill, and no expansion after a unit closes or blocks.

Group work only when cause, contract, implementation, or acceptance is genuinely shared. A coherent
change need not be split by finding ID. Necessary technical/test work for assigned criteria is in
scope; unrelated improvements and additional findings need authorization. Report critical release
blockers even when outside scope. Repair regressions caused by the assigned change without inventing
new product behavior; escalate a material product/security decision.

Preserve explicit and phase prerequisites. A prerequisite may be consumed before its separate
delivery only within the same authorized coherent delivery unit, with its complete criteria and
consumer/provider integration proved. Otherwise an unsatisfied out-of-scope prerequisite blocks
the dependent work; do not silently implement it or weaken acceptance.

Work sequentially through authorized units and their acceptance/delivery. A larger approved plan may
name one integrated delivery milestone; intermediate units still need their own technical evidence.
Do not impose per-finding CI inside that milestone or begin unapproved work while CI is pending.

## Safe implementation and runtime work

- Before editing, identify callers, contracts, tests, fixtures, generated outputs, and configuration
  actually affected. Prefer the smallest complete fix using existing patterns; no speculative
  abstractions, compatibility layers, refactoring, or dependencies. A necessary temporary adapter
  needs a retirement condition, not an indefinite second implementation.
- For bugs, establish the prior failure boundary before repair. Prefer an automated RED regression;
  critical logic/failure boundaries require automated coverage where executable. If automation is
  not reasonably possible, retain a precise reproducible manual check and explain the limitation;
  missing required proof remains incomplete. New functionality proves its acceptance criteria,
  not an invented historical defect. Never weaken assertions, thresholds, or guards to get green.
- Preserve unrelated worktree/index content exactly, including pre-existing `.pi/` changes. Do not
  use restore/checkout/reset/clean, stash, broad staging, or equivalents to manufacture a clean tree
  or hide work. Stage only accepted owned content, including carefully separated hunks when needed.
- Repository-local `music/` is a fully disposable test sandbox. External/user-owned music must not
  be inspected, imported, modified, or used as fixtures. Real `data/`, backups, configuration,
  `.env*`, credentials, and secrets are not fixtures. Synthetic test configuration/secrets are
  allowed only in explicitly disposable resources. Reset, backup/restore, and destructive
  administrative tests use isolated temporary roots/volumes, not repository music or real state.
- Main owns routine setup, slow tests/builds, diagnostics, cancellation, and teardown. Establish a
  supported stop/cleanup mechanism before starting fixture services. Prefer one managed runner owning
  start, tests, and teardown within the same sandbox execution. Do not let a launcher exit while its
  services remain alive expecting a later shell call to stop them. Interactive browser work needs a
  persistent controller verified compatible with the actual sandbox/tool backend; if unavailable,
  report the capability gap before launching services, without broadening permissions. Reuse adequate
  lifecycle evidence instead of adding repetitive preflights; cooperative cancellation tests do not
  prove forced tool-abort cleanup. Record ownership and stop only processes/containers/volumes proven
  to belong to this task. Runner exit or an empty process-name search does not prove descendants
  stopped. Unknown ownership is unknown, not permission to kill or to start a replacement writer.
- Respect real sandbox/tool restrictions and existing resource budgets. Diagnose a denial once
  within authorized capabilities; do not retry the same forbidden operation via another command,
  wrapper, agent, native API, or protocol. Report the exact blocker and required authorization.
  Do not edit permissions, protected configuration, or secrets to make a task executable.
- **Git credential-helper diagnostics on macOS:** `fatal: failed to store: 100001` may concern
  unnecessary storage of temporary sandbox-proxy credentials rather than failure of the Git
  operation. Do not assume either explanation from the message alone. Check the Git process's own
  exit code (not merely a pipeline/wrapper status) and expected result separately from helper stderr.
  Establish the cause within existing permissions using configuration provenance, credential-free
  proxy metadata, and applicable implementation evidence; proxy presence alone is not proof. Never
  print tokens, passwords, full authenticated proxy URLs, or raw credential traces. If Git succeeded
  and the evidence supports a proxy-credential storage failure, record it as non-blocking and continue with
  all normal acceptance/delivery checks. If the operation failed or the cause remains uncertain,
  diagnose within existing permissions; do not change credentials, helpers, or sandbox protections
  without authorization. Reuse an established diagnosis while relevant configuration remains unchanged.
- Do not remove caps, renew grants, change models/protocols, or treat unknown child accounting as
  zero. Before a known deadline/budget limit, checkpoint at a safe boundary and start no work that
  cannot finish safely. Reattach to a progressing job rather than rerun it; use native retry/backoff
  for transient failures. Waiting is not failure, product progress, or permission to expand scope.

## Acceptance, verification, and review

Four gates apply to the authorized endpoint:

1. **Requirements:** every assigned criterion and dependency is satisfied with scoped evidence;
   unresolved criteria remain explicit. A status edit or generic green suite is not acceptance.
2. **Verification:** test changed behavior, integrations, and relevant failures; run real browser
   E2E when the affected journey or explicit acceptance requires it.
3. **Review:** apply the plan's review/risk requirements, or the preventive matrix; no review for
   genuinely SIMPLE edits, except finding closure as specified above.
4. **Delivery:** when required, accepted content, scoped commit, safe push/integration, applicable
   local gates, and latest successful exact-SHA CI with a delivery receipt.

Map each material criterion to current content/revision, scenario or command, expected/observed
result, and relevant environment/image/artifact. Reuse adequate evidence when inputs, assumptions,
and environment still apply; rerun affected checks after changes and investigate unknown impact.
After a shared-boundary change, reassess affected open and closed obligations, not the entire repo.
Collateral closure is not automatic. Do not rewrite correct behavior just to produce a diff.

### Local and specialized gates

Use focused checks while repairing. Before delivery, freeze accepted content and run the complete
local suites of affected layers once on the final applicable content; do not repeat them after each
finding. Purely documentary SIMPLE changes need documentary checks, not artificial runtime suites.

Backend, from repository root:

```bash
uv sync --locked --extra dev --extra audio
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

Frontend, from `frontend/`:

```bash
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

E2E, from `e2e/`: `npm run test` when applicable; build the candidate frontend first.
Install locked dependencies using the repository/CI-supported options; do not resolve a different
runtime to obtain PASS. Provider tests use deterministic contract fixtures, not live services.
FTS5 tests use migrated databases; build-output fixtures must be deterministic, not ignored leftovers.

- OpenAPI changes require regeneration and an in-sync comparison of checked-in frontend types.
- Schema/DB changes require clean creation, supported migration paths and drift checks such as
  `alembic check` or the repository equivalent. Preserve the specification's pre-production versus
  stable-upgrade policy; neither permits destroying music or using real installations as fixtures.
- Filesystem, job, persistence, reset, security, and recovery changes require the corresponding
  positive/negative, fault, restart/retry, cancellation, and concurrency evidence.
- Docker/native/deployment changes use the exact identified image and isolated Compose resources.
  Prove functional `fpcalc` and `rsgain` as the runtime user; HTTP health/help output is insufficient.
  Apply relevant backup/restore, reset, hardening, and exact-runtime dependency audit gates.
- Explicit scale/performance criteria retain their workload, hardware, memory contract, and thresholds;
  never lower them after a run to obtain PASS. Full production readiness/whole-backlog completion
  requires cumulative applicable readiness gates on one final candidate/image and all required
  findings closed. A package's CI alone is not production readiness.

### Independent review: at most two automatic passes

Select effort from approved risk before launch: Sol Medium normally, Sol High for critical work.
Provide a fresh, self-contained assignment with obligations, dependencies, applicable contracts,
candidate/diff, tests/evidence, and risk/gates. Reviewer independently reads the assigned requirements
and connected implementation; acceptance review is not diff-only review. Check positive/failure
semantics and evidence adequacy. Reviewer uses read-only tools, does not execute shell/tests or
modify code, and reports limitations rather than claiming checks it did not perform.

Use at most **two automatic review passes per coherent unit/milestone**: one initial review and,
after accepted corrections, one targeted re-review of blockers, repair delta, and affected criteria.
Findings must be concrete, reproducible or structurally evidenced, and tied to assigned acceptance
or regressions. No stylistic scope creep or automatic refactoring. Main evaluates findings and the
active writer repairs accepted blockers. Non-blocking notes are explicitly justified.

An unexpected need for Sol High uses a fresh review assignment and consumes the remaining pass;
do not reset the count by rotating agents, renaming a unit, or requesting repeated reassurance.
If blockers remain after the second pass, stop the automatic repair/review cycle, checkpoint the
incomplete work, and report the blocker and smallest next action to the owner. Never lower
acceptance or declare completion to satisfy the limit. Review absence/infrastructure failure is
not approval. Preserve already valid checks and review when only unrelated content changes.

### UI/UX and browser evidence

Use **`design-taste-frontend`**, replacing `ui-ux-pro-max`, for relevant UI/UX implementation and
material UI review. Main loads it when applicable; attach it explicitly to Implementer, Reviewer,
and Browser-tester when their assignment requires it. Verify actual availability; a missing-skill
warning does not mean the skill was used. Pending installation/configuration is a capability gap,
not authority to silently use the removed skill or claim the migration operationally complete.

Product requirements, accessibility, approved functional UX/design, and the existing stack prevail.
Apply the skill to the assigned delta: no unsolicited redesign, style draw, new component library,
asset downloads, motion layer, or dependencies. App screens use its app guidance, not landing-page
rules. Full design workflow belongs only to an authorized design/redesign. Use scripts and visual
checks where they provide pertinent evidence; do not turn aesthetic preferences into new product
requirements or bypass the two-pass independent-review limit with another automatic fix loop.
Read-only reviewers may assess supplied screenshots/results without gaining script/shell access.

Browser-tester exercises relevant behavior through Chrome DevTools on the frozen candidate using
isolated fixtures: interactions, routing/state, real file flow, keyboard/focus, responsive/reduced-
motion behavior as affected. A backend change needs browser acceptance only when its contract affects
browser behavior or explicit criteria require it; reuse still-applicable evidence. Report flow,
expected/observed result, console/runtime errors, failed requests, reproduction, and PASS/FAIL.
Screenshots alone do not prove interactions, nor does browser PASS replace failure/recovery proofs.

## Goals, outcomes, and safe delivery

Only the owner starts each `/goal`, in a new Pi session. Main may work on ordinary explicit requests
without Goal mode; it must not turn them into goals, initiate the next goal, or resume a paused goal
without authority. Use only the adopted `@narumitw/pi-goal` lifecycle. Its command/tool presence does
not prove an active goal; runtime goal identity, states, waits, budgets, and guards remain authoritative.
Do not reproduce the extension's internal state machine in repository policy.

Before work, establish the authorized scope and endpoint. For an owner-started development goal,
normal commit/push to canonical `vertillo/muzilla` `refs/heads/main` is the default authorized delivery.
The owner may instead explicitly authorize local-only, no-commit, no-push, or internal verification
before work; honor that endpoint without calling it delivered. Do not reinterpret a failed delivery
as a local-only success. Ordinary non-goal work does not imply commit/push permission. Tags, release,
publication, deployment, permission changes, and history rewriting always need separate authorization.

Use precise outcomes:

- **IMPLEMENTED:** changes exist; remaining verification, acceptance, or delivery is named.
- **VERIFIED / INTERNALLY_VERIFIED:** all technical criteria and applicable review/gates passed on
  identified content. Terminal success only for an explicitly authorized internal/local endpoint;
  otherwise delivery is outstanding. This alone does not close a delivery-required finding.
- **DELIVERED / CLOSED:** criteria, dependencies, review, applicable local gates, exact-SHA CI and
  authorized delivery are proved. Use CLOSED for findings only after their closure requirements pass.
- **BLOCKED:** a concrete criterion or required delivery step cannot proceed within permissions after
  safe diagnosis; preserve evidence and state the resumption condition.
- **NEEDS_OWNER_DECISION:** an unresolved product, scope, security-policy, endpoint, or material plan
  decision prevents safe continuation. Report it without pretending the task is complete.

For default main delivery, Main:

1. Verifies canonical repository identity, configured upstream/target, baseline/candidate ancestry,
   worktree/index state, no unresolved merge entries, and preservation of unrelated content.
2. Commits only accepted owned content after applicable local gates/review. Inspect hook results;
   unexpected content mutations invalidate affected acceptance/checks and require repair. No empty
   commits or rewriting user history. Scoped checkpoint commits are allowed when authorized and
   useful, never to publish a known-broken candidate; `no commit` forbids them.
3. If accepted work is in another worktree/branch, advances local main only with a normal,
   conflict-free `git merge --ff-only <accepted-candidate>` after proving main is its ancestor and
   dirty state will be preserved. No automatic rebase, divergent merge/reconciliation, reset,
   stash/discard, force update, remote change, or target substitution. Stop on divergence, conflict,
   unexpected remote movement, or uncertainty.
4. Performs a normal fast-forward push to configured canonical main. Conflicts must be absent in
   both delivery and main worktrees. A rejected push is a delivery blocker, not permission to force.
5. Waits for the latest CI run for the exact final 40-character SHA. Current CI requires successful
   `backend`, `frontend`, `e2e`, and `docker` jobs; pending, failed, missing, cancelled, or skipped jobs
   do not pass. Local gates or an earlier successful run cannot substitute. This also applies when
   CI runs broader checks than the local change requires. A push submits the candidate; it does not
   prove acceptance. A later candidate commit needs its own applicable checks and exact-SHA CI.
6. Verifies local `refs/heads/main` and freshly observed canonical remote main both equal the final
   accepted SHA. Records SHA, repository/target, push result, CI run/attempt/URL and job results,
   criterion evidence, review/browser scope, generated/DB/image impacts, and residual risks.

For an explicitly different endpoint, apply its stated gates and describe exactly what was and was
not delivered. Never close a finding whose required main delivery or exact-SHA CI is still missing.
Keep ledger content anchored to verified implementation and preserve the final receipt in durable
session/CI storage; do not create an extra commit merely to embed its own SHA and invalidate CI.

Use supported execution/wait mechanisms for progressing tests, children, and CI, with a concrete
wake/monitor or bounded safety deadline where needed. Routine waiting is not a blocker and does not
require a costly specialist. Respect owner pauses and runtime limits; do not manufacture repeated
blocked turns, claim unsupported pause controls, or bypass guards to keep a goal running.

Call `goal_complete` only under the active runtime contract, with the current goal ID, after every
requirement for the authorized endpoint has evidence. Include or cite the criterion map, exact
content/SHA, applicable check/review/browser results, and required delivery/CI receipt. Internal-only
completion explicitly names the owner-authorized endpoint and absence of delivery. Neither a plan,
a budget limit, a blocker, nor an Implementer success message proves completion.

## Final handoff

Report delivered/closed IDs first with SHA and CI receipt, or say no new closures. Then distinguish
implemented, verified, and delivery-pending work. Name remaining criteria, exact blocker/owner decision
if any, and the smallest next action. Summarize affected checks, failure/browser/migration/generated-
contract/image evidence and limitations without dumping logs. Stop at the authorized scope/endpoint.

## Runtime adoption boundary

This guide describes the approved host workflow; it does not edit or grant runtime permissions.
Protected settings, agent definitions, extension selection, and skill installation require their
separate authorized configuration changes. Until adopted, do not invoke unavailable roles, nested
consultation, or skills, and do not fall back to the removed orchestration graph. Main's direct work
still obeys the effective runtime and safety boundaries.

The versioned Docker Pi sandbox remains **legacy and outside this migration**. Do not assume it has
the host's Pi, pi-subagents, Goal extension, model support, or role/skill configuration. Verify actual
capabilities before any use; align it only under a separate authorized task, without disabling its
protections or claiming host configuration migrated it automatically.
