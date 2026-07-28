# muzilla — gotchas & decisions ledger

Durable knowledge from Phases 0–5 that is **not derivable from the
repo**: facts about the world that cost real time to learn, and design
decisions whose *reasoning* would otherwise be lost.

Read `CLAUDE.md` for conventions and `docs/PLAN.md` for the
architectural contract.

> **What belongs here.** Only things a fresh session cannot recover by
> reading code or `git log`. Phase status, test counts, commit lists and
> file inventories are deliberately **not** tracked here — they live in
> git and go stale the moment they are written down. For what shipped in
> a phase, read the commits; for whether the tree is healthy, run the
> verification gate in `CLAUDE.md`.
>
> For a plan-vs-tree divergence audit as of 2026-07-27, see
> `docs/DRIFT_REVIEW.md`.

---

## ★ Load-bearing design decision: one release, one source

**Matching follows the beets model.** A candidate is one release from one
source. Picking it applies **all** of that release's tags. There is **no
per-field cross-source merging** — no `field_sources` config, no per-field
source dropdown, no per-field `provenance` column.

An earlier draft of the plan (and the Claude Design mock's right-hand
pane) specified per-field merging: title from MusicBrainz, label from
Discogs, genre from Deezer. **That is rejected.** It silently produces
metadata describing no actual release — e.g. MusicBrainz's tracklist for
a 2011 remaster paired with Discogs' catalog number for the 1999 original
pressing. Release-level coherence beats best-of-breed fields.

What this means concretely:

- The diff review screen's right pane is a **candidate picker** (ranked
  `(source, release)` rows), not a provenance panel. Picking a row
  re-stages the entire changeset.
- One source badge in the changeset header; **no source badge per diff row**.
- Candidates from different sources describing the same release stay as
  **separate pickable rows**, visually flagged as duplicate alternatives.
  Flagging is a hint only — it never merges data.
- `change_sets` carries `candidate_source` + `candidate_ref`;
  `changes` carries `is_manual`, not `provenance`.
- **Album art is the one exception** — chosen independently by quality,
  since Deezer/MusicBrainz carry no artwork of their own.
- In the review screen the only mutations are **accept / reject / edit the
  value**. Adding unproposed fields and bulk field-setting live in the
  separate manual editor.
- When porting the mock: `winnerOverrides` and the "use this" buttons must
  **not** be ported. Replace with a single `selectedCandidateRef`.

---

## Library-shape decisions

**`%aunique` is effectively mandatory in flat mode.** Every file shares
one namespace, so collision detection runs against the *entire library*
rather than per-directory. A rename job refuses to run while any
collision is unresolved.

**A field value containing a literal `/` is a validation error, not
something to sanitize away.** When `album_artist="AC/DC"` renders into a
path, the whole rendered string is checked uniformly — a stray `/` from
field data is flagged exactly like one the template wrote (or becomes a
real directory split in foldered mode). Letting it through unflagged
would be precisely the surprising, filesystem-risky behavior that the
"rendered `/` is a validation error" rule exists to prevent. A test
originally asserted the opposite; the *test* was wrong (see gotcha 17).

---

## Gotchas — facts about the world (don't re-discover these)

### mutagen / tag formats

1. **WAV/AIFF tag dispatch.** `mutagen.File()` returns `WAVE`/`AIFF`
   objects that are NOT `ID3FileType` instances, but their `.tags`
   subclass `mutagen.id3.ID3`. Dispatch on the **tags** class, not the
   container class, or WAV/AIFF silently read as empty.

2. **`VComment.pop(key, default)` (Vorbis comments) doesn't accept a
   default arg**, unlike a normal dict — raises `TypeError: pop
   expected at most 1 argument, got 2`. `tags/writer.py`'s Vorbis path
   uses a small `_vc_del()` helper (`if key in tags: del tags[key]`)
   instead. `MP4Tags`, by contrast, *is* a real dict subclass and
   `.pop(key, None)` works fine there — the two mutagen tag containers
   are not API-uniform despite looking similar.

3. **MP4 `cpil` (compilation) is a scalar `bool`, not a one-element
   list.** `tags.get(key, [False])[0]` looks correct and never fires
   against a fixture that omits `cpil`, but raises `TypeError: 'bool'
   object is not subscriptable` the moment a real `compilation=True`
   round-trips.

4. **ffmpeg here has no `libvorbis`** — use the native `vorbis` encoder
   with `-ac 2` (it only supports 2 channels). Only relevant when
   regenerating fixtures; CI never needs ffmpeg.

### SQLite / SQLAlchemy

5. **FTS5 needs real migrations in tests.** `Base.metadata.create_all()`
   does not create virtual tables or triggers. Use the `db_session`
   fixture (in the shared `tests/conftest.py`), which shells out to
   Alembic.

6. **`DateTime(timezone=True)` doesn't reliably round-trip tzinfo on
   SQLite.** SQLite returns a naive datetime even though the column is
   declared timezone-aware, so `row.expires_at <= datetime.now(UTC)`
   raises `TypeError: can't compare offset-naive and offset-aware
   datetimes`. Normalize to UTC on read before comparing.

7. **`session.add(child)` + setting a FK column directly leaves the
   parent's in-session relationship collection stale.** With
   `expire_on_commit=False` (this project's setting), `change_set.changes`
   is lazy-loaded once — empty, before any Changes exist — and nothing
   invalidates it. Queries that `select()` straight from the DB are
   unaffected, so the symptom is silent and shows up two calls later as
   `ValueError: no applied changes to undo`. Append through the
   relationship (`change_set.changes.append(change)`) instead; SQLAlchemy
   keeps collection and FK in sync that way. **Any future package that
   builds one-to-many rows inside a single session before commit needs
   the same care.**

8. **Keyset cursor must encode the sort value, not just the id.** An
   id-only cursor skips rows whenever sort order diverges from insertion
   order.

9. **`NOT IN (...)` hits the same SQL-variable limit as `IN (...)`, and
   doesn't chunk the same way.** `Column.notin_(ids)` binds one
   parameter per *excluded* id, so it crashes identically to an
   unbatched `IN()` once the excluded set is large enough — but you
   can't fix it by batching into several `NOT IN` calls OR'd together
   (that wrongly re-includes rows excluded by a different chunk). For
   `services/paths.py`'s collision check, the fix was to drop the SQL
   filter entirely and exclude the ids in Python after an unconditional
   column-scoped select — still one query, still no full-row load,
   just no `WHERE` clause that can blow the variable limit. See
   `db/batching.py` (docs/PLAN.md §11m) for the shared `IN()` chunking
   helper this doesn't apply to.

### Tooling / build

9. **import-linter's `forbidden` contract type is transitive by
   default.** It flags indirect imports too, so `api → services → db`
   broke the "API and CLI may only import services" contract even though
   that is the exact sanctioned path. Fix: `allow_indirect_imports =
   "true"` on that contract. The direct-import ban still works (verified
   by temporarily adding `import muzilla.db.models` inside `api/`).

10. **Docker: copy the frontend build BEFORE `pip install`.** hatchling
    snapshots package contents at install time, so installing first
    ships an empty `web/static/`. The wheel should be ~500KB, not ~12KB.

11. **`.dockerignore` needs `!README.md`.** A blanket `*.md` exclusion
    breaks the hatchling build, which reads the readme for metadata.

12. **`docker-compose.yml`'s `--env-file` doesn't reach the container.**
    `--env-file`/`env_file:` only populate `${...}` interpolation
    *inside the compose file* — they don't become the container's
    runtime environment unless an `environment:` block references them.
    A default `docker compose up` with a password set only via
    `--env-file` refuses to start, because the container never sees
    `MUZILLA_AUTH__PASSWORD`.

13. **hishel ≥1.0 is a ground-up rewrite.** The documented
    `FileStorage`/`Controller`/`AsyncCacheTransport` API only exists
    pre-1.0; 1.3.0 exposes `AsyncCacheProxy`/`AsyncSQLiteStorage` and no
    `FileStorage`. Pinned to `hishel>=0.1.0,<1.0`.

14. **`Unidecode` is GPL** and incompatible with this repo's MIT
    license — which is why `%asciify` hand-rolls transliteration
    (NFKD-decompose + strip combining marks, plus a small supplement
    table for non-decomposables like `ß`/`æ`/`ø`/`þ`).

15. **`npm ci` and `npm install --legacy-peer-deps` don't share
    peer-dep-resolution behavior** — `npm ci` enforces strict peer deps
    with no separate flag equivalent to `--legacy-peer-deps` on
    `install`; it must be passed to `npm ci` explicitly too. Found when
    §11i's `openapi-typescript` (stale `^5.x` TypeScript peer dep against
    this repo's TS 6) broke the Dockerfile's `npm ci` even though local
    `npm install --legacy-peer-deps` already worked around the same
    conflict.

16. **rsgain's build deps aren't just the ffmpeg/taglib/ebur128 set.**
    Its CMakeLists.txt also `pkg_check_modules`-requires `libswresample`
    (a separate package from `libavutil-dev` — FFmpeg splits resampling
    out), `inih` (`libinih-dev`), and `fmt` (`libfmt-dev`). None of these
    are exercised by local development (native deps installed once,
    directly, never through the Dockerfile's exact apt-get line) — only
    a from-scratch Docker build surfaces the gap. Verify any change to
    this dependency list against a real `cmake` configure/build, not by
    reading rsgain's install docs.

17. **A tag pushed by a workflow using `secrets.GITHUB_TOKEN` will not
    trigger another workflow that watches for that tag.** GitHub
    deliberately suppresses workflow runs for events authored by
    `GITHUB_TOKEN`, specifically to prevent accidental recursive
    workflow chains — confirmed against GitHub's own "Triggering a
    workflow from a workflow" docs, not assumed. `release.yml`'s
    version-bump tag push would otherwise never reach `publish.yml`'s
    `on: push: tags: v*.*.*`, shipping a tagged release with no GHCR
    image and no error. Needs a separate token (a fine-grained PAT or
    GitHub App installation token) with write access, stored as its own
    repo secret — `workflow_dispatch`/`repository_dispatch` are the
    documented exceptions that still fire.

18. **Prometheus reserves the `_total` suffix for counters, not
    gauges** — confirmed against Prometheus's own naming-convention
    docs. A metric that can decrease (our track/changeset/job counts
    can, since rows get deleted or move between states) shouldn't carry
    it even though "total count of X right now" reads naturally with
    the suffix; `promtool check metrics` flags the mismatch. Keep
    `_total` only on genuine monotonic counters
    (`muzilla_provider_requests_total`).

---

## The recurring lesson: unit tests against stubs miss whole bug classes

Five separate bugs across four phases shared one shape — **a unit test
constructed its own inputs, so the bug lived in the wiring the test
bypassed.** Recorded together because the pattern matters more than any
single instance.

15. **`Track.format` looked wired but wasn't.** `db/models.py` had the
    column and `tags/reader.py` set `codec`, but nothing populated
    `format`. Nothing crashed — the field was just always `None`, until
    `services/analyze.py`'s format breakdown came back empty against
    real scanned data. Unit tests that construct `Track` rows directly
    set every field explicitly and could never have caught it.

16. **Nothing ever ran Alembic outside the test fixture.** `muzilla
    scan`/`serve` both assumed an already-migrated DB. A fresh
    `docker compose up --build` passed `/api/health` but 500'd on every
    `/api/tracks` call (`no such table: tracks`) — SQLite happily
    creates an empty file on connect, so nothing failed loudly until a
    query ran. Fixed by running `alembic upgrade head` from the API
    lifespan and the scan/analyze CLI commands. Only findable by
    actually running the container; every test builds its DB through
    the `migrated_db` fixture, which bypasses this gap by design.

17. **`differ.diff_field()` assumed every field name is in the
    `domain.fields` registry.** Grouping corrections stage pseudo-fields
    (`track_ids_add`, `is_pinned`) describing `TrackGroup` mutations,
    not track tags. `changes/`-level unit tests only ever exercised
    track-entity diffs, so the `KeyError` surfaced as a 500 from the
    `/groups` endpoint tests. Fixed with a documented fallback:
    unregistered names render as a generic `kind="scalar"` diff.

18. **`build_provider_set` at app startup broke every existing API
    test.** Wiring `ProviderSet` into the lifespan meant every test that
    boots the app creates on-disk cache dirs under
    `MUZILLA_STORAGE__CACHE_DIR`, whose packaged default (`/data`) is
    only writable inside Docker. Found by running the *whole* suite
    after adding the endpoints, not just the new tests.

19. **`%aunique` value-vs-field-name bug — and the integration test that
    almost missed it too.** `DisambiguationResolver.resolve()` correctly
    returns *which field* separates a collision (`"year"`), but
    `_unique_impl` rendered that field *name* in brackets (`" [year]"`)
    instead of the track's *value* (`" [1999]"`). Stub-based unit tests
    had been written to a consistent-but-wrong contract, so they passed
    against the bug. Only a real `TrackGroup`/`Track` DB fixture caught
    it — and even that test's first draft passed with a loose
    `"1999" in ... or "2010" in ...` assertion, because the fixture set
    `year` on the `TrackGroup` rows but not on the `Track` rows that
    `track_to_variables` actually reads. **Both the implementation and
    the test's assertion tightness needed fixing.**

**A related inversion (worth its own note):** sometimes the *test's
premise* is what's wrong. `test_each_component_sanitized_independently`
assumed a stray `/` in a field value would be silently sanitized;
reviewing the implementation against design intent showed the
implementation was right and the test backwards. Don't reflexively trust
the test over the code — reason about which one encodes the intent.

20. **`group.members.remove()` needed alongside `session.delete()`, not
    instead of it — the same gotcha #7 pattern, in the deletion
    direction.** `pipeline/duplicates.py`'s reconciliation pass deleted
    a stale `DuplicateMember` row via a bare `session.delete(member)`;
    with `expire_on_commit=False`, the already-loaded `group.members`
    in-session collection never learned the row was gone, so a fresh
    query of the same object inside the same test still saw it. Fixed
    by removing through the relationship (`group.members.remove(member)`)
    *and* deleting the row, same as #7's append-side fix. Caught by an
    integration test asserting post-reconciliation membership — a test
    that stubbed the DB layer couldn't have seen it, since the bug is
    entirely about session-cache/DB divergence.

21. **A `FieldDiff`'s binary summary can be wired into `diff_field()`
    and still always render "none → none."** `changes/differ.py`
    accepts `old_binary_summary`/`new_binary_summary` params and has
    since the art diff kind was designed, but every caller of
    `diff_field()` left them `None` — the one real call site
    (`services/changesets.py`'s `_diff_for_change`) never looked up the
    `Blob` row to build one. Nothing raised; the field diffs just
    always rendered "none" regardless of actual content. Only visible
    by actually staging an `embed_art` change and reading the API
    response, not by unit-testing `diff_field()` in isolation (which
    passes explicit summary strings as test input and never exercises
    the caller that's supposed to compute them).

### Performance / scale (docs/PLAN.md §11g — the 100k-track pass)

**Recorded numbers, against a real 100,000-track scratch library** (the
raw measurements PLAN.md §11g's own text points here for): cold scan
74s, warm rescan 7.4s (matches §7's "a 100k rescan should take seconds"
claim), `GET /api/tracks` first page 93ms / deep cursor page 53ms
(cursor pagination stays flat with depth, not degrading with offset),
FTS5 search 10-39ms across queries returning 1k-13k results, grouping
cascade 36s for the whole library, `/rename` preview over 1k tracks
474ms (after fixing the two bugs in gotcha 23 below; see gotcha 22 for
the crash this scale first surfaced).

22. **SQLite's `IN (...)` variable limit is 999 on old builds, 32766 on
    newer ones (SQLite ≥3.32) — don't assume either without checking,
    but always batch.** `pipeline/grouping.py`'s fingerprint-consensus
    stage passed every not-yet-grouped track id as bind parameters to
    one `TrackFingerprintMatch.track_id.in_(remaining_ids)` call; a
    100k-track cascade run raised `sqlalchemy.exc.OperationalError: too
    many SQL variables` outright. No test before the §11g performance
    pass ever ran the cascade above fixture scale, so this had never
    fired. Fixed by batching at 500 ids per query, comfortably under
    either limit — don't rely on runtime detection of the actual
    ceiling, since it depends on the SQLite build.

23. **A confirmed-by-inspection N+1 fix that barely moves the number is
    a sign to profile, not to stop.** `services/paths.py::preview_rename`
    calling `_group_kind()` (one `session.get()`) per track was flagged
    as a likely N+1 by inspection before ever measuring; fixing it
    alone took a 1k-track `/rename` preview from 2.9s to only 2.96s.
    Profiling (`cProfile`, not more guessing) found the actual dominant
    cost sitting right next to it: the batch collision check loaded
    every OTHER track in the library as a **full ORM object**
    (`select(Track)`, ~99k rows including JSON-column genre/artists/mood
    deserialization) just to build a `path -> id` lookup dict. A
    column-scoped `select(Track.id, Track.path)` fixed that one; the two
    fixes together brought the same call to 474ms (6.25x). Lesson: an
    N+1 spotted by reading the code and the actual bottleneck under load
    are not guaranteed to be the same line — profile before declaring
    a performance fix done.

24. **A performance-test generator that copies a real, pre-tagged
    fixture file inherits every field it doesn't explicitly overwrite —
    identically, across every copy.** `scripts/gen_perf_library.py`
    (new) copies `tests/fixtures/audio/silence.mp3` (real Sigur Rós
    tags from `tag_fixtures.py`'s `COMMON` dict) to synthesize a 100k-
    track scratch library. Two baked-in fields it didn't clear broke
    the grouping cascade's results, not its runtime: every track
    silently shared the fixture's `mb_release_id`, so Stage 1 merged
    all 100k tracks into one release; every track silently shared
    `track_total=10`, so `_apply_partial_album_flag` misclassified most
    real albums as `partial_album` (`track_count` 2-4 vs "expected" 10).
    Neither raised an error — the cascade "succeeded" with wrong
    results, caught only by actually inspecting `track_groups` after
    running it, not by the absence of a crash. A third, unrelated
    generator bug (artist names sharing a textual prefix, e.g.
    `"Artist {i}"`, score as near-identical under the cascade's fuzzy
    string-distance clustering and get transitively single-link-merged)
    produced the same "everything in one group" symptom for a
    completely different reason — worth remembering that this failure
    mode has more than one possible cause.

---

## Design decisions whose reasoning isn't in the code

**Why per-field cross-source merging was rejected** — see the
load-bearing decision at the top of this file.

**Why `paths/` stays fully DB-free.** PLAN §6 says `%aunique` "needs DB
context," but the layering table lists `paths → domain` only. Resolved
with a `DisambiguationResolver` Protocol in `paths/context.py`; the
DB-backed implementation lives in `services/paths.py`. This keeps the
whole template engine unit-testable with zero DB fixtures.

**Why `%time` requires `%%` escaping.** A bare `%Y` inside a template
argument is indistinguishable from an attempted `%Y{...}` function call
to the lexer, which has no per-function argument semantics. Requiring
`%time{$date,%%Y-%%m-%%d}` reuses the escape the engine already defines
for a literal `%`, rather than teaching the lexer per-function rules.

**Why `$albumartist` works despite the canonical field being
`album_artist`.** PLAN §6's example templates use beets' names. Rather
than renaming the canonical fields or breaking the plan's examples,
`render.py` aliases the beets spellings onto canonical values after the
normal mapping pass. `domain/fields.py` remains the single source of
truth.

**Why `applier.py` takes `library_root`/`create_directories` as explicit
parameters** rather than calling `load_config()` internally — the global
config singleton is an anti-pattern this project deliberately avoids
(PLAN's "Where beets should NOT be copied"). This is why `WorkerContext`
carries a `config: Config` field.

**Why a 2-of-4-tracks-present album still scores as a confident match.**
This looked like a `missing_tracks` weight miscalibration worth fixing.
It isn't: muzilla is a metadata tool, not a rip-completeness verifier.
"I only ever wanted these 2 songs" is legitimate and common, and PLAN
§7b assigns the incomplete-rip-vs-deliberate-subset judgment to the
*grouping* cascade's `partial_album` flag, not to matching (whose only
job is "is this the right release"). The corpus fixture's *expectation*
was corrected, not the weights — **don't "fix" this during a future
weight-tuning pass.**

**Why roman-numeral unification is scoped to ordinal markers.** A naive
`\b(roman-numeral-shaped-token)\b` regex converts ordinary English words
spellable from roman-numeral letters — "Mix" (M+IX) became "1009";
"Civic" and "Live" were also at risk. Conversion only fires directly
after a recognized marker ("Pt.", "Vol.", "No.", "Disc", …).

**Why sparse local fields must be omitted from the distance denominator,
not scored 1.0.** Treating "no local barcode/ISRC/per-track artist" as a
full mismatch systematically inflates distance for exactly the
sparsely-tagged, era-varying files this library shape is full of.

**Why rename staging is synchronous.** It returns a DRAFT ChangeSet
exactly like `find-replace`/`strip` already do — only *apply* goes
through the job queue. No new async plumbing, no new apply endpoint.

**Why multi-value fields join with `', '`** when rendered into a path
variable — the only existing precedent in the codebase (`TagEditor.tsx`,
`CandidatePicker.tsx`); the Python side had never needed to join them to
a display string before.

**Why duplicate detection has no delete/resolve action.** PLAN
§Phase-6 says "duplicate detection by fingerprint, not filename" —
detection only. Which copy to keep is a product judgment (bitrate?
format? tag completeness? which one has better tags?) the plan doesn't
specify, and file deletion has no ChangeSet/undo precedent anywhere
else in the codebase — every other mutation path is reversible by
design, and a delete-and-forget action would be the first that isn't.
`DuplicateGroup.dismissed` covers the one real need (false positives,
e.g. a live take AcoustID matches to the studio recording's id)
without inventing a destructive action the plan never asked for.

**Why `embed_art`/`write_lyrics` don't use `Change.old_value`/
`new_value` the way every other op does.** Art needs a blob reference
(`old_blob_id`/`new_blob_id`) since binary content doesn't belong in a
JSON column; lyrics is large free text with a `synced` flag that
needs to travel with it, so it's `{"text": str, "synced": bool} | None`
rather than a bare string — `Change` has no per-row metadata column,
and the JSON `new_value` column is the only place that pair could ride
together to `changes/applier.py`, which reads `synced` back out to set
`Track.lyrics_synced` on write.

**Why ReplayGain is staged per-group, not per-track.** Album gain
requires analyzing an album's files together in one `rsgain`
invocation — it isn't decomposable per-file the way track gain is.
Singletons (one-track groups, §7b) flow through the same function
uneventfully; there's no separate singleton path.

---

## Deliberate deferrals (still open)

- **No `/settings` page** for *any* config value, so PLAN §6's "template
  editor in settings" has no home yet. `/rename` covers the
  per-selection case with inline validation + live preview. A global
  default-template editor is a separable follow-up.
- **`settings` and `users` tables** (PLAN §5) do not exist. Auth reads
  the password from env, so no user table is needed yet; both land
  whenever DB-backed config/auth actually needs them.
- ~~Blob storage has no producer~~ — resolved in Phase 6:
  `pipeline/enrichment.py`'s art fetch calls `BlobStore.put()`, and
  `GET /api/blobs/{id}?size=thumb` serves the bytes back to the diff
  review UI.
- **Idempotency is in-process**, keyed on `(path, Idempotency-Key)` in
  `app.state`. Sufficient for a single-container app with single-writer
  SQLite; revisit with a DB-backed table if multi-worker deployment
  becomes real. Note it currently covers only `changesets` apply/undo,
  not every mutating endpoint as PLAN §10 specifies.
- ~~No OpenAPI→TS codegen~~ — resolved in Phase 7 (§11i): generation is
  wired (`frontend/src/lib/api-types.ts`, generated from the live
  FastAPI schema) with a CI diff check that fails the build on drift.
  **Still open**: the generated types aren't consumed anywhere yet —
  `frontend/src/lib/api.ts`/`types.ts` (hand-written since Phase 1)
  still drive every request. Migrating page-by-page, one PR per page
  each verified live, is the recorded follow-up (PLAN.md §11i, Risk #8).
- ~~No Hypothesis property tests~~ — resolved in Phase 7 (§11f):
  `tests/tags/test_writer_properties.py` and
  `tests/paths/test_parser_fuzz.py` found and fixed 3 real bugs (year
  tag writes were a no-op, ID3 multi-value genre/mood truncated to one
  value, ~500-deep nested `%func{}` templates raised `RecursionError`
  instead of `TemplateError`).
- **Testing-strategy gaps still open:** scoring corpus is 13 scenarios
  against PLAN's ~50, format matrix omits WavPack/WMA/DSF, and there is
  no ported beets template-compatibility suite. See
  `docs/DRIFT_REVIEW.md`. Explicitly out of scope for Phase 7 (§11a) —
  not pulled forward.
- **Phase 6's new frontend (Duplicates page, enrichment trigger buttons,
  art-thumbnail diff rows) passed lint/typecheck/build but was never
  clicked through in a real browser** — this sandbox has no outbound
  network access, so nothing could drive a running dev server. Worth an
  actual click-through before relying on it, same caveat as any
  frontend change landed without one.
- **`docker/Dockerfile`'s pinned rsgain source build (v3.4, added
  Phase 6) has never actually been run** — `docker build` wasn't
  exercised this session. The checksum is real (copied from the local
  complexlogic/tap Homebrew formula, verified 64 hex chars), but the
  cmake/apt-get sequence itself is unverified until someone runs
  `docker compose up --build`.

## Phase 8 security audit sweep (docs/PLAN.md §12c, step 2.9)

Four items investigated; two are confirmed non-findings recorded here so
a future session doesn't re-audit them from scratch, one dependency
finding was fixed, one was investigated and left open with reasoning.

- **Blob path containment — confirmed safe, no fix needed.**
  `services/blobs.get_blob_bytes` takes `blob_id: int`, looked up via
  `session.get(Blob, blob_id)`; the actual file path
  (`changes/blobstore.py::get_bytes`) reads `blob.storage_path`, a DB
  column derived server-side from `sha256[:2]/sha256[2:4]/sha256` at
  `put()` time. No request-controlled string ever reaches path
  construction — `api/routers/blobs.py`'s `blob_id: int` path parameter
  means FastAPI rejects a non-integer segment before the handler even
  runs. Traced the full call chain to confirm, not just the top-level
  signature.
- **Secrets in logs — confirmed safe, no fix needed.** Grepped every
  `.get_secret_value()` call site (`config/schema.py`'s
  `resolved_token`/`resolved_password`, `services/auth.py`'s
  `_session_secret`): none of their return values reach a log call.
  The provider HTTP client's response-logging choke point
  (`providers/cache.py::_log_response`) logs only
  `response.request.url.host` + method + status — never the full URL
  (which would leak AcoustID's `client` query-param API key) or any
  header (which would leak Discogs's `Authorization: Discogs
  token=...` header). `services/migrate.py` is the only other structured
  log call touching `config`, and it logs `db_path` only.
- **Dependency audit — one real, fixed; one real, deferred.**
  `pip-audit` (via `uvx pip-audit -r <(uv export --no-hashes)`, since
  `uv`'s own venv has no `pip` module for pip-audit's default scan
  mode) found nothing against the resolved Python dependency set.
  `npm audit --omit=dev` found `react-router` 7.12.0–8.2.0 vulnerable to
  GHSA-qwww-vcr4-c8h2 (CSRF bypass), but only in **RSC Mode** — this app
  uses plain `BrowserRouter` client-side routing (verified: no
  `react-server`/RSC usage anywhere in `frontend/src/`), so the
  vulnerable code path is unreachable here. No non-breaking fix exists
  yet (the only available fix is a downgrade to 7.11.0, or a jump to the
  8.3.0+ line); revisit when a patched 7.x lands. `npm audit` (full,
  dev-inclusive) additionally found `js-yaml` 4.0.0–4.2.0 (quadratic-CPU
  DoS, GHSA-52cp-r559-cp3m) and `brace-expansion` (unbounded-expansion
  DoS) as transitive deps of `@redocly/openapi-core` (dev-only, the
  OpenAPI-codegen tool — outside `--omit=dev`'s scope but fixed anyway
  since dev tooling still runs in CI and locally). `brace-expansion` was
  resolved by `npm audit fix --legacy-peer-deps` alone;
  `js-yaml` needed an explicit `overrides` entry in `package.json`
  pinning it to `^4.3.0`, since `npm audit fix` wouldn't bump a
  doubly-nested transitive dependency on its own. `pip-audit` added to
  the backend CI job.
