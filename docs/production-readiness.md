# Production readiness

This document defines the durable completion contract. It does not certify any historical
commit or image. Evidence belongs in CI artifacts, release records, or a task handoff, not as
permanent “currently green” prose here.

Muzilla is fully ready only when all conditions below pass on the same candidate revision,
the exact candidate image where applicable, and [completion-matrix.md](completion-matrix.md)
contains zero actionable rows.

Muzilla is not currently production-ready while any completion row or readiness gate remains
open. Production-ready means the completion matrix is empty, every required gate passes on
the same candidate, and that candidate satisfies the documented supported environment and
hardware contract.

## Source and change control

- The candidate is an identified full commit SHA with a clean, reviewed diff and generated
  files in sync.
- Unrelated user changes are excluded. No release, tag, publish, push, or deployment is
  implied by readiness; those require explicit authorization.
- Every closed matrix item has implementation evidence and a regression/acceptance check at
  the boundary where the old behavior failed. Documentation-only closure is invalid.
- Product behavior matches [product-spec.md](product-spec.md), or an intentional contract
  change updates the specification and its tests in the same reviewed work.

## Backend gate

Run from the repository root:

```bash
uv run ruff check src tests
uv run mypy src
uv run lint-imports
uv run pytest -q --cov=muzilla --cov-report=term-missing
```

The gate must pass without weakening assertions, broad skips, warning suppression that hides
runtime defects, or tests reading user-owned data. Coverage must meet the repository/CI
threshold and include negative behavior for changed boundaries.

## Frontend and generated API contract

Run from `frontend/`:

```bash
npm run lint
npm run typecheck
npm run test
npm run build
```

Regenerate OpenAPI types using the repository command and require a clean generate-and-diff
result. Frontend server types derive from the generated schema; local adapters are limited to
view concerns and SSE/other contracts that OpenAPI cannot express. No casts or parallel manual
schemas may conceal drift.

## Browser journeys

Build the packaged frontend before E2E, then run from `e2e/`:

```bash
npm run test
```

The complete deterministic suite covers at least:

- contained file/directory import, positive automatic match, rejected/zero-result/provider-
  failure outcomes, and cancellation;
- manual search and every supported provider URL type, including SSRF/invalid-type negatives;
- stable ReviewBundle creation, optional enrichment, typed edits, cover actions, decisions,
  filtered navigation, and explicit bulk reject;
- apply success, validation/runtime failure rollback, exact file/catalog verification, restart
  recovery, persistent undo, deterministic undo failure/recovery, and retry;
- single-file reread/analyze-again and missing-file behavior;
- duplicate evidence, Activity diagnostics, Settings/effective policy, provider secret
  persistence/redaction, retention expiry, and both reset scopes;
- auth/logout, CSP/security headers, keyboard focus, responsive/mobile behavior, and the
  explicit manual accessibility/responsive checks retained in the completion matrix.

Provider behavior uses deterministic contract fixtures and a scoped mock. Live provider tests
are opt-in diagnostics, never a default readiness dependency.

## Database and migrations

- A clean database migrates to head and starts successfully.
- Every supported upgrade origin migrates to head with data and workflow semantics preserved;
  downgrade is tested where the migration contract promises it.
- Before the first stable release, migrations may be squashed or reorganized and development
  installations may recreate the internal database and rescan the library. A reliable clean
  database creation path is required, and this policy must never delete or modify music. After
  the first stable release, supported upgrades preserve persistent application state through
  proper migrations.
- Migration tests run against real migrated SQLite fixtures, including FTS5 objects.
- `alembic check` (or the repository's equivalent compare) reports no drift while excluding
  only explicitly managed virtual/shadow objects.
- Additive/destructive compatibility transitions, reset recovery, and restart during a
  migration-adjacent state fail safely and are documented.

## Dependency and runtime audit

- Python and frontend runtime dependency audits have no unaccepted exploitable findings.
  Accepted findings require a documented scope, rationale, compensating control, and expiry.
- Lockfiles are reproducible; fresh installs use the same dependency groups as CI and the
  image build.
- Deprecated runtime APIs and framework upgrade warnings are resolved or explicitly bounded.
- Native dependencies are verified in the distributed image, not inferred from host tests.

## Exact-image and Compose acceptance

Build one candidate image and use its immutable reference for all image-level checks. Verify:

- non-root UID, dropped capabilities, `no-new-privileges`, resource/log limits, and expected
  mounts/ownership;
- liveness, readiness, storage probes, and capability responses;
- `rsgain` and `fpcalc` execute functionally with their shared libraries against disposable
  audio as the runtime user;
- the packaged SPA and generated API contract are the candidate's build, not stale local
  artifacts;
- isolated Compose startup, authentication, scan/review/apply/undo, restart, Settings/secret
  persistence, and reset use temporary bind mounts and uniquely named volumes;
- exact-image verification rejects an image/SHA mismatch before publication.

A passing `/api/health` alone is insufficient.

## Apply, rollback, recovery, and undo

Use disposable audio fixtures to prove:

- source drift, missing files, symlinks, containment escape, collision and case-only rename
  behavior fail at the intended boundary;
- per-file temp write, fsync, no-clobber replace/move, catalog/stat/hash reconciliation, and
  backup failure semantics;
- zero accepted operations cannot enqueue or apply;
- validation errors write nothing and runtime failure/cancellation rolls back a ReviewBundle
  through its journal/recovery path before reaching a terminal state;
- per-file committed/rolled-back/failed/skipped results remain visible, retry is allowed only
  from deterministic recovery, and no workflow reports a silently partial ReviewBundle;
- cancellation is observed only at safe checkpoints and cannot bypass the same atomicity or
  journal invariants;
- injected crashes before and after file/DB checkpoints reconcile deterministically after a
  new process/session;
- undo uses a frozen persistent inverse, is idempotent across restart, restores exact tags,
  lyrics, embedded art and paths, and fails closed on expired journal, drift, collision, or
  uncertain recovery;
- ReviewBundle atomicity is explicit, while no workflow claims that the general filesystem
  provides a global transaction.

## Backup and restore

- Document and verify a cold archive of the stopped `/data` volume to operator-controlled
  external storage with a SHA-256 checksum.
- Restore only after checksum verification into a new, empty destination volume.
- Start the exact image validated for that state and verify migrations, settings, managed
  secrets, catalog/review/journal history, and restart persistence.
- Explicitly exclude `/music` and operator-owned environment/configuration/secrets from the
  `/data` archive and document their separate backup responsibility.
- A failed checksum, non-empty destination, wrong image, or partial restore must fail before
  overwriting existing state.

## Reset safety

Both reset scopes are verified on temporary filesystem roots and volumes only. Tests assert:

- persistent maintenance lock, in-flight mutation drain, external-worker lease handling,
  quiesce, idempotent replay, startup recovery, and fail-closed partial cleanup;
- same-origin, session-bound CSRF, current-password/confirmation rules, audit, and session
  revocation;
- allow-listed database deletion and safe cache/blob/managed-secret cleanup;
- rejection of broad, related, overlapping, symlinked, changed, or non-directory targets;
- catalog reset preserves settings/secrets; factory reset removes managed settings/secrets;
- music and backup hashes (and inode where meaningful) remain unchanged.

No reset test may point at a repository `music/`, `data/`, `.env*`, configuration, secret, or
user library path.

## Provider secrets and external failures

- Managed provider tokens persist across restart/recreate, take effect in the published
  runtime snapshot, and can be replaced/cleared safely.
- Tokens are absent from API responses, generated schemas, logs, audit rows, SQLite/WAL,
  errors, diagnostics, and ordinary backups/exports where the contract excludes them.
- File/directory permissions and references are allow-listed and fail closed on migration,
  write, checkpoint, resolver, or runtime-refresh failure.
- Disabled, not configured, checking, operational, invalid credentials, temporary failure,
  permanent failure, not found, and cancellation remain distinguishable after restart.
- Retry budgets, `Retry-After`, backoff/jitter, cache behavior, and stale probe generation are
  deterministic in tests.

## Scale and operational behavior

- Performance is checked at a representative workload of at least 100,000 tracks, including
  albums, singles, duplicates/near-duplicates, incomplete/inconsistent metadata, multidisc,
  compilations, multi-artist cases, Unicode/case edges, embedded art, and ambiguous matches.
  Relevant I/O/audio-tool paths use realistically sized audio files and SSD is the baseline.
  The exact benchmark hardware is recorded. The full workflow covers cold/incremental scan,
  Catalog/search/filters/facets, grouping, matching, ReviewBundle generation/navigation,
  Apply, Undo, cancellation, throughput, responsiveness, memory, and resource behavior.
  Initial Muzilla-owned memory target is 2 GiB; Docker Engine, OS filesystem cache, and
  unrelated services are excluded. Quantitative thresholds are fixed before the run and may
  not be relaxed afterward merely to obtain PASS. No hardware-independent total runtime is
  required yet. Any increase above 2 GiB requires quantitative evidence, bottleneck analysis,
  a proposed new minimum, and documented trade-offs.
  Catalog/review/facet queries use keyset pagination and bounded queries; batch operations stay
  within SQLite variable and connection limits.
- Memory, CPU, database connection count, provider concurrency/rate limits, and on-disk cache
  retention stay within documented deployment limits.
- Restart during scan, enrichment, apply, undo, reset recovery, and ordinary idle operation
  produces an explicit recoverable state and no false success.
- Metrics and logs avoid secrets and unnecessary file paths, identify user actions and
  technical correlation when needed, and do not require full-table work on routine scrape or
  Dashboard load.

## Documentation and fixture hygiene

- `AGENTS.md`, `README.md`, `CONTRIBUTING.md`, this readiness contract, the product spec, and
  the completion matrix agree and contain no dead links or obsolete workflow instructions.
- README claims are supported by the candidate's current code and evidence; one-off counts or
  past CI results are not presented as durable truth.
- Tests create deterministic temporary build output and fixtures. They never inspect, modify,
  import, reset, or delete real music, `data/`, `music/`, `.env*`, configuration, backups, or
  secrets.
- Provider tests do not contact live services by default. FTS5 tests use migrated database
  fixtures. Container tests use explicit candidate image references and isolated resources.
- `git diff --check` passes and repository links resolve.

## Final stopping condition

Readiness is achieved only when:

1. every gate above passes on the same candidate revision/image;
2. every supported migration, restart, recovery, backup/restore, reset, provider-secret, and
   primary user journey has current evidence;
3. documentation and generated contracts are consistent; and
4. [completion-matrix.md](completion-matrix.md) has zero actionable items.

Publication or deployment remains a separate explicitly authorized action.
