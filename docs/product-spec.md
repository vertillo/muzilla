# Muzilla product specification

This document is the normative product contract for Muzilla. It describes the intended
product, not an implementation chronology or a production-readiness claim. Known deviations
from this contract are listed only in [completion-matrix.md](completion-matrix.md); readiness
is defined in [production-readiness.md](production-readiness.md). The current repository is
not production-ready while the matrix or any required readiness gate remains open.

## Product scope

Muzilla is a self-hosted, single-user music metadata manager. It reads local audio files,
indexes their current state, retrieves coherent metadata candidates, prepares changes for
review, and writes only the operations the user explicitly applies.

Muzilla is not an audio player, playlist manager, or listening-library manager. It does not
take ownership of the user's directory layout. Its primary deployment is one container with
SQLite and a bind-mounted library, and its design target is at least 100,000 album tracks and
loose singles with uneven tags in a flat library.

The filesystem is the source of truth. The database is a rebuildable index plus durable
workflow, journal, audit, configuration, and recovery state. External file changes must be
detected and must never be silently overwritten from a stale database snapshot.

## User mental model

The primary unit is a file, or a coherent set of files, that needs metadata work. Users
should not need to understand database groups, legacy ChangeSets, retention sweeps, or
provider adapter internals.

A `ReviewBundle` is the stable user-facing review. Technical work such as scanning,
matching, cover retrieval, lyrics retrieval, fingerprinting, and ReplayGain remains split
into independently retryable tasks. Those tasks converge on one review; they do not become
one monolithic job or a fictitious global transaction.

Current, proposed, and attempted state are always distinct:

- current state is what the last verified file snapshot contains;
- proposed state is an immutable review revision;
- attempted state records apply or undo outcomes, including per-file results and any
  recovery-required state.

## Information architecture

The primary navigation contains:

1. **Dashboard** — library health and direct links to the work behind each actionable
   metric;
2. **Catalog** — known files, search and filters, duplicate evidence, file detail, and
   single-file actions;
3. **Reviews** — the ReviewBundle inbox and review detail;
4. **Activity** — user actions and their outcomes, with technical jobs/events available as
   diagnostics;
5. **Settings** — providers, effective policy, retention, storage information, and safe
   administrative actions.

The web UI is Muzilla's primary interface. Import is a supported journey launched from it;
the CLI is support and troubleshooting tooling, not a competing primary review interface.
Duplicates belong in Catalog, grouping uncertainty belongs in Reviews, and raw jobs are
diagnostic detail rather than separate products. Legacy ChangeSet routes are not part of the
target information architecture.

Navigation state is durable and comprehensible. Search, filters, sort, current item, and a
validated internal return destination live in URL state where appropriate. Browser Back
uses real history; Close uses the validated origin; previous/next navigation stays in the
current filtered order without losing the list position.

## Import and scanning

The user may select supported files or directories inside the configured library root and
review the resolved scope and active policies before starting. A web deployment browses the
server-side mounted library; it does not pretend to upload or browse the client filesystem.
Containment and symlink protections apply before work is enqueued.

Scanning reads filenames, file facts, tags, embedded art state, and supported audio facts.
It writes no music. An incremental scan reuses unchanged files and marks missing or
unreadable files explicitly. A corrupt, unreadable, or unsupported file is reported for that
file and does not abort the rest of the scan. A single-file reread contacts no provider.
“Analyze again” may chain reread, fingerprint/ReplayGain, and matching according to policy,
but later stages do not start after an unsuccessful reread.

The primary production target is local storage mounted into Docker on an SSD. Initial
production readiness covers relevant case-sensitive and case-insensitive filesystem semantics;
NAS/NFS/SMB are best-effort and are not required to close the initial gate. No read, write,
rename, move, Apply, recovery, reset, or undo operation follows a symlink outside the
configured library root.

Format support is documented from implementation and test evidence, not assumed from a file
extension. Each format is classified as supported, best-effort, or unsupported. A format is
supported only when its scan, reviewed write, Apply, and undo behavior are verified; uncertain
support remains an explicit compatibility gap and is never silently accepted. Current
disposable tag round-trip evidence covers MP3, FLAC, Ogg/Vorbis, Opus, M4A, WAV, and AIFF;
that evidence alone does not claim full reviewed-write support. WavPack, WMA/ASF, and DSF
remain unverified and are not advertised as supported until the compatibility backlog closes.

Cancellation is cooperative and visible. Work already indexed remains valid; unfinished
tasks are marked cancelled; no proposal is published from an in-flight task after its
cancellation checkpoint. A single file's protected commit is not interrupted halfway.

## Matching and candidates

Muzilla gathers candidates from enabled metadata providers, hydrates a bounded shortlist,
ranks complete candidates locally with weighted signals, computes confidence, and rejects
candidates that lack sufficient identity or exceed the absolute mismatch threshold. Filename
inference may fill missing local metadata but must not override trustworthy tags without
evidence.

Matching is review-first and applies nothing on its own. There is no auto-apply in any mode:
not in the web UI, not in the CLI, and not through quiet, batch, script, or high-confidence
shortcuts. Candidate decisions use exactly three bands:

- **strong** — a strong match may automatically preselect its best candidate into a
  ReviewBundle. Preselection is only a convenience: the candidate remains fully editable and
  is applied solely through the user's explicit Apply in the web UI;
- **ambiguous** — the review shows an ordered candidate list with raw scores, meaningful
  differences, and provenance/context, and Muzilla never silently chooses one. The user must
  explicitly select a candidate or choose Skip / Leave unchanged; until one of those happens
  the item is unresolved;
- **reject** — candidates below the reject threshold never appear in the user's choices and
  cannot be selected, force-matched, or applied. If no candidate clears the reject threshold
  the file stays unmatched and needs attention, and the user resolves it explicitly with
  Skip / Leave unchanged.

No filesystem-changing metadata operation bypasses ReviewBundle, and an unresolved item blocks
Apply of the entire ReviewBundle.

A selected candidate represents one recording or release from one provider. Its metadata is
coherent and is not assembled by silently merging fields from different releases. Secondary
providers may contribute only explicitly supported complementary enrichment; identity-defining
fields from the primary candidate are not silently overwritten. Provider priority is a ranking
tie-breaker, not a field-merging rule. Cover art is the deliberate exception: it may be
selected independently when it can be reliably associated with the selected release, and its
source stays visible.

Candidate snapshots preserve provider identity, type/reference, artist/title/release,
year, duration, track position/count when known, thumbnail, confidence band, raw scoring
signals, penalties, provenance, and rejection reasons. Unknown values remain unknown. Manual
selections are labeled manual and do not invent a confidence score. If the user has manually
changed a value in a review, a later provider refresh asks for confirmation before replacing
that value with refreshed remote metadata.

Provider zero results, disabled/not-configured state, invalid credentials, temporary
failure, permanent failure, and cancellation are distinct outcomes. A failed provider does
not erase successful results from another provider or collapse into “no result.” Provider
preference is configurable as a tie-breaker only: it cannot make a clearly worse candidate
beat a materially better candidate from another provider.

## Manual search and candidate URLs

Manual search lives inside a ReviewBundle and starts from normalized file/tag/filename
facts. Users can refine title, artist, release, year, duration, and identifiers and select
which capable providers participate. Results are hydrated, ranked, paginated explicitly,
and retain provenance.

Supported provider URLs are parsed by a pure allow-listed registry into provider, item type,
and provider ID. The backend fetches that ID through the configured provider API; it never
proxies or fetches an arbitrary user-controlled host. Unsupported types, local/private
hosts, credentials, ambiguous authorities, ports, and encoded path tricks fail before
networking. Re-selecting the same candidate is idempotent and does not create another inbox
item or redundant revision.

## ReviewBundle behavior

A bundle is created with a stable identity before matching completes. No hit, a rejected
automatic candidate, or a provider failure therefore produces a visible review requiring
attention rather than an absent result.

The inbox is file-first. It shows filename, path, physical state, proposed identity,
confidence, provider, and blocking issues without hover. It uses indexed keyset pagination,
searchable/combinable URL-backed filters, and an explicit “load more” action. Applied and
archived reviews are reachable but are not the default queue.

Review detail combines, when applicable:

- source file identity and immutable snapshot;
- selected candidate and its explanation;
- typed metadata operations;
- final filename/path preview and collision state;
- current and candidate cover art;
- lyrics source, synced/plain state, and typed editing;
- ReplayGain values and runtime capability state;
- preparation tasks, sanitized failures, and scoped retry actions;
- apply and undo runs with per-file outcomes.

Operations are a discriminated contract such as `SetTag`, `WriteLyrics`, `EmbedArt`,
`RemoveArt`, `MoveFile`, `SetReplayGain`, and `GroupingCorrection`. Decisions persist per
operation. Editing creates a new immutable revision while preserving unrelated decisions.
Zero accepted operations cannot be applied; archiving/rejecting proposals writes no file
and remains reversible until another action makes that impossible. ReviewBundle is the only
review model; legacy ChangeSet APIs, services, persistence, and terminology are transitional
implementation surface rather than a second product model.

A review item is either unresolved or resolved. Unresolved means no applicable decision has
been made: an untouched candidate outcome, or an ambiguous candidate with no explicit choice.
Resolved means the item has an accepted candidate, an explicit rejection/archive, or an
explicit Skip / Leave unchanged. An explicitly skipped item is not modified, counts as
resolved, is visibly distinct from an unresolved item, and never blocks Apply of the rest of
the bundle. Apply of the entire ReviewBundle is disabled while any blocking condition exists —
an unresolved ambiguous item, a path collision, a stale source, a concurrent conflict, or any
other bundle-level validation failure — and there is no "Apply only the ready items" shortcut
that bypasses the bundle contract.

ChangeSet is legacy. The target architecture removes `/changes`, `/edit`, `/rename`,
`/api/changesets`, legacy ChangeSet services, persistence, compatibility-only adapters, and
old UI terminology once their ReviewBundle-native replacements are complete. Backward
compatibility for these pre-production interfaces is not required, and pre-production
ChangeSet application state need not be preserved.

Bulk rejection acts only on explicitly selected reviews, previews the count, never means
“everything matching this hidden filter,” and offers a reversible undo affordance.

## Enrichment

Metadata, embedded cover art, lyrics, and ReplayGain may be prepared automatically according
to effective settings. Optional tasks do not block opening the review. Each section exposes
pending, running, ready, not-found, configuration-required, transient failure, permanent
failure, and cancelled states where applicable. Retry is limited to the failed, retryable
scope and does not recreate the bundle.

Cover selection, removal, and provider fetch are proposals until apply. Muzilla uses only
artwork retrieved from supported remote sources. Local artwork sidecars such as `cover.jpg`,
`folder.jpg`, and `album.jpg` are ignored: they are not inputs, outputs, artwork sources, or
mutation targets. Embedded art is the primary art model for the flat-library use case. Art
may come from a provider different from the primary metadata provider when it is reliably
associated with the selected release, and the source remains visible. Automatic selection
uses deterministic release/quality criteria, while the user can change the selected remote
artwork in review. Replacing existing embedded art shows old and new art in ReviewBundle,
journals the change, and supports undo. No remote artwork is a valid outcome: metadata review
and apply remain valid without art.

Lyrics edits always operate on the text payload, preserve provenance, and validate synced
timestamps. ReplayGain analysis is non-mutating; calculated values become ordinary reviewed
tag operations. If the distributed runtime lacks the native capability, the UI explains the
unavailable state instead of offering an action destined to fail.

## Apply, retry, recovery, and undo

No scan, provider fetch, candidate selection, edit, enrichment action, or review decision
writes music. All music-file mutations use the protected reviewed apply path. There is no
production mode in which a strong match writes directly without explicit review execution.
Apply of metadata and file changes is exclusively a web UI action: the CLI is support and
troubleshooting tooling that may inspect and prepare review state but never applies a
ReviewBundle or auto-applies a match, and it offers no `--yes`/`--quiet`/`--force`
equivalent for metadata or file Apply.

Before any file is written, Apply validates the complete ReviewBundle for library
containment, symlink rules, source snapshot/stat/hash preconditions, destination safety, and
collisions. It writes accepted tag, lyrics, embedded-art, and ReplayGain changes to temporary
files, flushes durable state, replaces without clobbering another file, and performs reviewed
moves where required. A source file changed externally after preview blocks Apply for the
entire ReviewBundle and requires refresh/re-review. The journal and catalog are reconciled
after a crash or restart.

ReviewBundle is atomic by default. Validation errors write nothing. A runtime Apply failure or
safe cancellation rolls back committed effects through the journal/recovery path before the
bundle reaches a terminal failed or cancelled state. The persistent result still identifies
per-file committed, rolled-back, failed, and skipped operations, and recovery remains
explicit if rollback cannot finish; Muzilla never silently leaves a partially applied bundle
or presents enqueue success as Apply success. Retry is allowed only from a deterministic
recovery state and skips effects already proven committed.

Two jobs or reviews that target the same file do not use last-writer-wins. The conflicting
operation is blocked with an explicit conflict, and refresh/re-review is required where
appropriate. ReviewBundle atomicity is a reviewed apply/recovery contract, not a claim that
the general filesystem provides a global transaction.

Undo is a separate persistent run against a frozen inverse manifest and the same writer and
journal protections. It runs effects in reverse side-effect order, observes cancellation
only between safe file boundaries, skips files already restored on retry, and fails closed on
expired journal data, drift, collision, or uncertain recovery. Its per-file outcomes and any
recovery requirement remain visible; Muzilla does not rewrite history to pretend the original
bundle never happened.

Destination collisions are never resolved by invented names or implicit suffixes such as
`(2)`, numeric/hash variants, `%aunique`, or `%sunique`. Muzilla detects collisions before
Apply, shows every conflicting file and destination, and blocks the entire ReviewBundle until
the user changes metadata or path configuration. The preview is recalculated before Apply is
allowed again. Collision checks cover case-sensitive and case-insensitive filesystem behavior,
Unicode normalization, and races that appear after preview; no operation clobbers a file.

## Catalog and file detail

Catalog search treats user text literally and never exposes database query syntax. Large
collections use indexed cursor/keyset pagination. Desktop uses an accessible data grid with
sortable and resizable bounded columns whose local preferences can be reset and persist;
mobile uses readable record cards. Essential state is textual, never color/icon/tooltip
only.

Filters are searchable and useful at high cardinality, combine predictably, appear as
removable active chips, and persist in the URL. Missing files remain queryable but are
excluded from actions that require a present file.

File detail separates reread, search for candidates, manual review creation, and analyze
again. A missing file shows its last path and observation and offers safe verification and
catalog actions. Every metadata edit enters the same ReviewBundle workflow.

## Duplicate handling

Duplicates are evidence, not an automatic deletion decision. One normalized evidence model
records fingerprint/recording identity, confidence, duration comparison, format/bitrate and
a documented quality assessment. The UI explains why files were grouped and what confidence
means, supports dismissing a false positive, and never chooses or deletes a file on the
user's behalf.

## Grouping

Grouping is an internal inference mechanism for coherent album/singleton work. It is not a
stable user-owned collection model and has no general CRUD or arbitrary cross-album
reassignment UI/API.

Uncertain grouping becomes a ReviewBundle operation with a consequence preview. The
resolver may confirm the current inference, treat a file as a single track, or move it only
to a demonstrably compatible inferred collection. Apply revalidates compatibility against
the current catalog; cancellation or failure never silently reassigns files.

## Scale and performance

The minimum production scale target is 100,000 tracks. The representative workload includes
albums, loose singles, duplicates and near-duplicates, incomplete and inconsistent metadata,
multi-disc releases, compilations, multi-artist cases, Unicode and case-sensitivity edge
cases, embedded artwork, and ambiguous matches. Meaningful portions of I/O-intensive and
audio-tool workloads use realistically sized audio files rather than only tiny synthetic
files.

The official Docker Compose deployment targets 2 GiB of RAM for Muzilla-owned processes,
including backend, worker, and same-deployment frontend processes. Docker Engine, the OS
filesystem cache, and unrelated services are outside that budget. SSD is the baseline. A
performance run records hardware and measures the full workflow: cold and incremental scan,
Catalog/search/filter/facet use, grouping, matching, ReviewBundle generation and navigation,
Apply, Undo, cancellation, responsiveness, throughput, memory, and resource behavior. It
does not use a hardware-independent total completion-time requirement. Quantitative
thresholds are fixed before the run and cannot be relaxed afterward merely to obtain PASS.
If the 2 GiB target cannot be maintained without materially compromising functionality or
usability, the evidence, bottleneck, proposed new minimum, and trade-off are documented
before changing the requirement.

## Settings, providers, and retention

Settings displays effective values, and every enabled control must affect runtime behavior.
It covers provider enablement and write-only credentials, provider preference order, Advanced
matching controls, automatic enrichment policy, filename templates and collision behavior,
strip rules, and the undo/history retention policy. Bootstrap storage and authentication
locations may be read-only when they cannot be safely changed at runtime. Inert “coming soon”
controls do not belong in the finished UI.

Settings → Advanced matching exposes only useful high-level controls: the weights for the
principal matching signals, strong/ambiguous/reject thresholds, the minimum gap between the
first and second candidate, and provider preference order. Defaults are safe; validation
rejects semantically invalid negative values, keeps controls within reasonable ranges, and
enforces internally consistent thresholds. The UI provides Reset to defaults and shows raw
matching scores in candidate review. Low-level implementation knobs are not exposed. Provider
preference is only a tie-breaker and cannot override a materially better candidate from another
provider.

Provider settings are published as an atomic runtime snapshot. Existing request/job leases
finish on their snapshot; new leases see the new configuration. Provider status uses
explicit labels, last-check time, and sanitized diagnostics. Secrets are stored outside
SQLite as owner-only opaque files; the database stores only references. Secrets never
appear in responses, logs, audit rows, WAL content, or ordinary exports.

Retention is presented as the undo/history window, not as a maintenance job. The policy
explains both age and count limits, their effective values, and the consequence of expiry.
The underlying sweep is hidden from normal Activity and may appear in system diagnostics.

Provider retrieval uses a persistent application cache with versioned entries, TTL, manual
refresh, provenance, and explicit stale/offline state. When a provider is unavailable,
Muzilla falls back to the most recent cached value; stale cached data may be used offline but
is clearly marked stale. Cached remote artwork needed by an existing review is retained for
review/apply continuity. Cache reset may remove cached metadata and artwork. Offline mode
does not discover metadata that Muzilla has never retrieved.

Relevant non-secret configuration, including Advanced matching settings and provider order,
survives container recreate/upgrade through persistent storage. Normal reset preserves this
configuration and references to externally supplied secrets; an explicit factory reset may
remove them. Preferred secret delivery is through Docker secrets, environment variables, or
deployment configuration, and the UI must still allow configuring provider credentials when
they are not managed externally. When an external secret and a UI-managed credential coexist,
the external secret takes precedence: the UI indicates the credential is externally
managed/configured, never overwrites it accidentally, and a UI-managed value is never used
while an external one is active. A secret entered through the UI is persisted appropriately,
survives recreate/upgrade, and is removed by factory reset. The UI may report configured,
missing credentials, or authentication/configuration error, but Muzilla does not require an
application-owned secret vault for the initial production contract.

Muzilla sends no external telemetry or analytics by default. Any future external reporting is
explicitly opt-in.

While Muzilla is pre-production, migrations may be reorganized or squashed, and development
installations may be required to recreate the internal database and rescan their library. This
policy never deletes or destructively modifies the user's audio library. A reliable clean
database creation path always exists. After the first stable release, supported upgrades
preserve persistent application state through proper migrations.

## Reset and administration

Reset catalog and activity removes only allow-listed Muzilla index, review, job, provider
cache, cached remote artwork, and other regenerable managed state. It preserves persistent
configuration, references to externally supplied secrets, managed provider credentials, auth
sessions, bootstrap configuration, backups, and music.

Factory reset additionally removes database settings overrides and managed provider
credentials and revokes all sessions. It requires the current password, the exact displayed
phrase, same-origin and session-bound CSRF validation, and a persistent idempotency key. The
factory scope is explicit and separate from normal reset.

Both scopes acquire a persistent maintenance lock, drain mutating requests, quiesce workers,
reject unsafe/overlapping/symlinked storage roots before deletion, and recover or fail closed
after restart. Neither scope owns the library, backup directory, or operator-provided
environment/configuration files.

## Security and architecture invariants

- Preserve mandatory single-password authentication, session revocation, rate limiting,
  security headers/CSP, trusted-proxy allow-listing, path containment, non-root container,
  dropped capabilities, resource limits, and secret redaction unless an equal or stronger
  replacement is verified.
- `domain/fields.py` is the canonical metadata field registry.
- API and CLI depend on services, not database models. Import-linter contracts define the
  authoritative layer direction.
- Mutagen and SQLAlchemy remain synchronous; async belongs at HTTP and worker boundaries.
- Provider search summaries and hydrated candidate details are different contracts.
- Frontend server contracts come from generated OpenAPI types. View adapters may rename or
  compose them but must not recreate server schemas manually.
- Provider tests use deterministic contract fixtures by default, never live services.
- Tests use only disposable audio, database, storage, configuration, and secret fixtures.

## Interaction quality

Every core action is reachable by keyboard and pointer with visible focus. Shortcuts do not
fire from inputs, editors, or dialogs. Status always has text. Dialog focus returns to its
trigger. Lists and review state remain usable at 200% zoom, low viewport height, mobile
safe-area layouts, long text, and reduced motion. Errors identify the affected item, explain
what remained unchanged, and say whether retry is safe.
