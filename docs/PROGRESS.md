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

## Phase 8 resource budget (docs/PLAN.md §12d, step 3.2)

- **The "orphaned blob on journal prune" leak the step brief predicted
  does not exist — confirmed by tracing, not assumed.** The brief's
  premise was that `ApplyJournal.before_blob` holds a blob reference
  that `sweep_apply_journals`'s bare `session.delete(journal)` leaks.
  `pipeline/retention.py`'s own docstring already asserted the opposite
  (`ApplyJournal` holds no blob reference at all), and tracing
  `before_blob`'s actual construction confirms the docstring, not the
  brief: `changes/applier.py::_meta_to_field_dict` builds it from
  `dataclass_fields(TrackMeta)`, which are scalar tag fields only —
  `art_blob_id` lives on `Track`, not `TrackMeta`, so no blob id (or
  anything else blob-shaped) is ever written into `before_blob`'s JSON.
  Art refcounting happens entirely at apply time, independently, via
  `changes/applier.py::_rebalance_art_refcounts` retaining
  `Change.new_blob_id` and releasing `Track.art_blob_id`'s old value —
  a path `sweep_apply_journals` never touches. Separately checked
  whether `ChangeSet` deletion orphans `Change.old_blob_id`/
  `new_blob_id` the same way: no code path anywhere calls
  `session.delete()` on a `ChangeSet` (`discard`, named in the model's
  state-machine docstring, has no implementation — retention only ever
  sets `state="undo_expired"`), so that cascade never fires either. No
  fix applied; this entry exists so a future session doesn't re-open
  the same investigation from scratch.

## Phase 8 longevity verification (docs/PLAN.md §12d, step 3.4)

- **hishel's on-disk HTTP cache was genuinely unbounded — confirmed by
  reading hishel's own source, not assumed.** `providers/cache.py::
  build_http_client` constructed `hishel.AsyncFileStorage` with no
  `ttl`. `AsyncFileStorage._remove_expired_caches` (hishel's own GC
  hook, called opportunistically on every `store()`) returns
  immediately when `self._ttl is None` — so with `ttl` unset, no file
  in `storage.cache_dir` was ever deleted, regardless of how often
  `sweep_provider_cache()` prunes the separate DB-side `ProviderCache`
  rows. Fixed by passing `ttl=190 days` (comfortably above
  `TTL_FINGERPRINTS`, the longest per-operation DB-cache TTL at 180
  days, so hishel's file GC never evicts an entry the DB-side semantic
  cache still considers fresh and forces a needless refetch).
- **A manual edit's Change starts at `decision="pending"`, not
  `"accepted"`.** `changes/builder.py::default_decision_for_kind` only
  auto-accepts `strip_tags` (for always-strip fields),
  `grouping_correction`, and `enrichment` sources — `manual_edit` is
  not in that list. `POST /changesets/{id}/apply` enqueues the job
  regardless, but `applier.py` only ever writes `decision == "accepted"`
  changes, so applying a freshly-staged manual edit without first
  `PATCH /changesets/{id}/changes`-ing it to `accepted` silently
  applies nothing. Relevant to anyone scripting the API directly (as
  this step's container measurement did) rather than going through the
  review-screen UI, which already handles this.
- **A single-track apply is too fast to reliably land a restart in the
  middle of it.** Attempted to verify restart-mid-apply recovery
  against the running container by starting an apply job and issuing
  `docker restart` immediately after; the job had already reached
  `succeeded` by the time the restart signal took effect (confirmed via
  container logs — job start and job end both logged before the
  "Shutting down" line). The container-level pass this step ran is
  therefore a clean-restart smoke test (migrations re-ran, applied
  state persisted, `recover_stuck_jobs`/`recover_apply_journal` both
  ran without error), not a genuine race — the actual mid-flight
  recovery behavior is what `tests/changes/test_apply_journal_
  recovery.py` and `tests/services/test_jobs.py` already exercise at
  the unit level with deliberately-stuck fixtures, which is the right
  place to test a race precisely, not a real container.

## Phase 8 frontend test infra (docs/PLAN.md §12e, step 4.3)

Two genuine product findings surfaced while writing Playwright coverage
against the real app rather than assumed behavior — recorded here so
Phase 6 (screen-flow fixes) doesn't have to rediscover either:

- **Catalog's "no results" empty state shows the wrong message for a
  search with zero matches.** `Catalog.tsx`'s empty-state branch reads
  `total === 0 ? "Run muzilla scan..." : "No tracks match..."`, but
  `total` (`data?.pages[0]?.total`) is the **search-scoped** count from
  the API response (`db/repo/tracks.py::list_tracks`'s `total`), not
  the overall library count. So searching a non-empty library for a
  term that matches nothing also has `total === 0`, and the user sees
  "Run `muzilla scan <path>` to index your library" — the message
  written for a genuinely empty library — instead of "No tracks match
  the current search and filters." `e2e/tests/catalog.spec.ts`
  characterizes the actual (wrong) behavior rather than the intended
  one. This is the exact defect class Step 5.4 targets (search/filter
  empty states going stale under a query); worth fixing there rather
  than tracking as a separate item.
- **None of the five `grouping_correction` actions (pin, merge, split,
  reassign, force-to-singleton) apply themselves.**
  `services/grouping.py`'s five call sites for these all build and
  return a `ChangeSet` via `build_changeset` — none call `apply` or
  enqueue an `apply_changeset` job. `default_decision_for_kind`
  auto-accepts the resulting changes, but "accepted" only means
  *ready* to apply, not applied — nothing in `Groups.tsx`'s Pin/Merge
  button handlers applies the changeset either. So clicking Pin never
  flips `Group.is_pinned`, and clicking "Merge into" never actually
  merges the groups in `/api/groups`'s response — both silently no-op
  from the user's perspective beyond the merge-mode banner clearing.
  `e2e/tests/groups.spec.ts` asserts this directly (`is_pinned` stays
  `false` after clicking Pin). Not a Phase 4 fix — flagged for Phase 7
  alongside suggestion #6 ("the grouping workspace is missing half its
  actions"), since fixing it either means auto-applying these
  changesets (a product decision about whether pin/merge should be
  instant vs. reviewable) or adding an explicit apply step to the UI.

Also: `e2e/tests/fixtures.ts` gained a second fixture (`authTest` /
`muzillaAuth`) for `auth.spec.ts`, since the original `muzilla` fixture
hardcodes `auth.enabled: false` so every other spec can drive the API
directly without a login step. Two fixture copies of the same source
file (`scanOneFile('a.mp3')` + `scanOneFile('b.mp3')`) share identical
tags, including `mb_release_id`, so `groups.spec.ts`'s merge tests
needed to break that identity via a real manual-edit -> accept -> apply
round trip before the cascade would produce two separate groups to
merge — a plain DB write would have been simpler but breaks the
"everything goes through the real API" convention every other spec in
this suite follows.

## Phase 8 accessibility pass (docs/PLAN.md §12e, step 6.5 item 6)

**Moving focus onto a button inside a `useEffect` that runs synchronously
during the same keystroke that opened it can make the browser "click"
that button immediately — via the *keyup* half of the triggering key,
not anything React does.** Implementing `Modal.tsx`'s focus trap (move
focus onto the first focusable element on open) broke
`ChangeSetReview.tsx`'s Enter-opens-Apply-modal flow: pressing Enter
correctly called `setConfirmAction('apply')`, the modal correctly
mounted, its effect correctly focused the close `<button>` — and then
the modal immediately closed again, every time, 100% reproducible.
Root cause: a native `<button>` fires a `click` event on the keyup of
Enter or Space *whichever element has focus at that moment* — and
`page.keyboard.press('Enter')` (or a real keypress) delivers `keydown`
first, then `keyup`. If a synchronous effect moves focus onto a button
in response to the `keydown`, that button is what receives the
still-in-flight `keyup`, and the browser interprets it as the user
pressing Enter *on the button*, firing its `onClick` — in this case,
the modal's own close handler, closing the modal the instant it opened.
Not caught by reading the code (the logic that fires `onClose` was in
`Modal.tsx`'s Escape-key branch, nowhere near the focus-move code) —
only surfaced by an e2e regression, and even then required binary-
searching which of six files touched in the same step actually caused
it (`git stash`ing files one at a time) before adding temporary
`console.log`s inside the keydown handler, the `setConfirmAction`
call site, and the modal's own `onClose` prop to catch the spurious
call with a stack trace. Fixed by deferring the initial focus move
with `setTimeout(0)`, so it runs on a fresh task after the triggering
keystroke (both keydown and keyup) has fully finished processing.
Worth remembering for *any* future auto-focus-on-mount logic that
might be triggered from a keyboard handler, not just this one.

Separately: `Modal`'s focus-trap `useEffect` originally depended on
`[open, onClose]`, but every caller in this codebase passes `onClose`
as a fresh inline arrow function on every parent render — meaning the
effect (and its cleanup, which restores focus to whatever was focused
before the modal opened) was re-running on every unrelated re-render
of the page behind the modal, not just on actual open/close. Fixed by
storing `onClose` in a ref and dropping it from the dependency array,
so the effect only depends on `open`. This was a real bug independent
of the focus-steal issue above — worth checking any component whose
effect depends on a caller-supplied inline callback prop.

## Phase 7 — approved product-design suggestions (1, 2, 3, 5, 6, 7)

Six of eight Phase 7 suggestions were approved and implemented in one
pass; #4 (light-theme toggle) and #8 (resource-tradeoff, N/A — Step 3.4
never found one) were not in this batch. Order actually landed:
1 (faceting), 2 (Dashboard) + 7 (provider health, built together per
the brief's own coordination note), 3 (Settings), 6 (grouping
auto-apply), 5 (Tailwind migration, done last as the highest-mechanical-
risk item).

### Server-side faceting (#1)

**`_scalar_facet`'s subquery approach breaks for genre specifically.**
`db/repo/tracks.py::get_facets` computes artist/album/format facets by
building a subquery from the shared filtered `_base_query()` and
`GROUP BY`-ing a column on it — straightforward. Genre can't use the
same path: it's a `JSONList` column (one row holds an array), so a
plain `GROUP BY tracks.genre` groups whole arrays, not individual
values — needs `json_each()` unpacked per row instead. Mixing an ORM
entity column (`Track.id.in_(...)`) into a statement that also has a
`text()`-defined `FROM`/`JOIN` clause (needed for `json_each`) makes
SQLAlchemy's ORM-aware compiler misdetect the whole statement as an
ORM-context select and fail with `'TextClause' object has no attribute
'selectable'` — not obviously connected to the actual mistake by the
error text. Every attempt to keep it as one clever mixed statement
failed differently; the fix that actually worked was mundane: fetch
filtered ids first via a plain column-scoped ORM select, then run a
separate parameterized raw-SQL `json_each` query batched through
`db/batching.py`'s existing chunking helper (same "large `IN()` over
SQLite" shape as `pipeline/grouping.py`'s fingerprint-consensus stage,
gotcha #22 above) — two round trips, not one, but each one is simple
and type-checks cleanly.

**Scope decision: facet options are scoped to the current search
string only, not to other active facets.** "Narrow as you go" (each
dropdown reflects every *other* active facet) is the more common
faceted-search pattern, but it needs a separate query per dropdown
(each excluding its own filter) and this library's four facets aren't
a strict hierarchy — an album can carry multiple genres in a
mixed-tag library, so options disappearing out from under a
half-built filter combination reads as broken more often than helpful
here. Recorded in `useTrackFacets.ts`'s own docstring so a future
session doesn't need to rediscover the reasoning from a commit body.

### Dashboard (#2) and provider health (#7)

**`services/analyze.py::analyze_library` is the wrong data source for
a Dashboard summary.** It loads every non-missing `Track` row into
Python to compute field-completeness/duplicate-candidate detail — the
right cost for an on-demand `muzilla analyze` CLI report, wrong for a
summary endpoint that loads on every visit to `/`. `services/
dashboard.py` is a separate, new module: every field is a single SQL
`COUNT`/`GROUP BY`, no full-table row load. Also: its album/singleton
split reads `TrackGroup.kind` (the real, cascade-derived grouping,
docs/PLAN.md §7b) rather than `analyze_library`'s `(album,
album_artist)` tag-only heuristic — the two numbers are **not** the
same thing and will disagree on a library with tag-only-groupable
tracks the cascade hasn't matched to a release yet.

**A real, blocking deadlock found by a hanging test, not by reading
the code.** `providers/status.py`'s first draft had `all_statuses()`
acquire `_lock` and then call `get_status()` (which also acquires
`_lock`) from inside that same `with` block. `threading.Lock` is
non-reentrant, so the second acquire blocks forever — the *first* real
call to `all_statuses()` (which the Dashboard's provider-health panel
would have made on every load) would have hung that request
permanently. Surfaced as `pytest` printing 6/7 dots then silently
stalling with no error, no traceback — took several minutes of
`Bash`-timeout debugging (collection was instant, execution hung,
narrowing it down to "which specific test" via `--collect-only` and a
process-list check) before spotting the double-acquire by inspection.
Fixed by extracting a lock-free `_to_status()` helper both entry
points call after acquiring the lock exactly once. Worth remembering:
a hung (not failing) test process, especially one whose collection
step is instant, is a strong signal to check for a non-reentrant lock
double-acquire before assuming it's an infra/network issue.

### Settings (#3)

**The "settings DB table" `config/schema.py`'s own docstring promised
did not exist.** `Config.settings_customise_sources` documents a
precedence chain including "... -> settings DB table -> MUZILLA_* env
vars -> ..." but no such table, model, or read path existed anywhere
in the codebase — it was purely aspirational. Built it as `db.models.
Setting` (plain `key`/JSON-`value` rows, not one column per setting,
so the covered-settings surface can grow without a schema migration
each time) plus a migration (0010) and `services/settings.py`.
Deliberately did **not** rearchitect `load_config()`/`settings_
customise_sources` to actually read from this table at bootstrap:
`load_config()` runs before any DB connection exists (the DB path
itself is a config value), so nothing DB-backed could affect bootstrap
settings (storage, auth) regardless. The table only backs settings
read *after* the app already has a session — which happens to be
exactly the three things Phase 7 suggestion #3 named (providers/
tokens, templates, strip rules).

**Scope decision: weights are explicitly NOT wired to a settings row,
on purpose, not by oversight.** `matching/weights.py`'s `ALBUM_WEIGHTS`/
`TRACK_WEIGHTS`/`SINGLETON_WEIGHTS` are hardcoded module constants
imported directly by `matching/engine.py`'s scoring functions — no
existing parameter seam to hang a settings override on. Making them
genuinely configurable means threading a weights parameter through the
whole matching call chain, which is a matching-engine refactor with
real correctness risk (this is the code that decides whether two
releases are "the same"), not a settings-storage problem. The Settings
screen shows an explicit "coming in a future release" placeholder
rather than either skipping the section silently or wiring a control
that would silently no-op. If a future session tackles this, the right
starting point is `matching/engine.py`'s scoring function signatures,
not `services/settings.py`.

**Provider enable/token settings persist but don't take effect without
a restart — this is a real, documented limitation, not a bug.**
`services/providers.py`'s `ProviderSet` is built once at app startup
from `app.state.config` with no rebuild hook. Rebuilding it on every
settings write (or re-reading settings on every provider call) would
defeat the point of building httpx clients once with warm hishel
caches — out of scope for the same "real architectural change, not a
storage one" reason weights are. Filename templates and strip rules do
**not** have this problem, because `services/paths.py` and `services/
strip.py` both already read their effective config fresh per request
(`effective_paths_config()` layers the DB override onto the file/env
`PathsConfig` on every call) — so those two settings take effect
immediately, and the Settings screen's copy says so explicitly per
section rather than applying one blanket caveat to all of it.

### Grouping workspace auto-apply (#6)

**Two additional, pre-existing bugs were only reachable once auto-apply
made these changesets actually apply for the first time.**
docs/KNOWN_BUGS.md #3 was "none of the five grouping actions apply
themselves" — fixing that (via `services.changesets.apply_now`, the
existing synchronous apply entrypoint, safe here specifically because
a `grouping_correction` changeset never touches files) immediately
exposed:
- `changes/applier.py`'s `_apply_group_changes` never updated
  `TrackGroup.track_count` when `track_ids_add`/`track_ids_remove`
  moved tracks — only the grouping cascade (`pipeline/grouping.py`)
  had ever written that column. A merge would leave the destination
  undercounted and the emptied source group's count stale forever.
  Both were silently wrong, not crashing, because nothing had ever
  exercised the apply path for a `grouping_correction` changeset
  before. Fixed by recomputing `track_count` via a plain `COUNT` for
  every group touched by an applied changeset.
- `services/grouping.py::split_group`'s own docstring claimed it
  "splits tracks out ... into new singleton groups (one per track)",
  but the implementation only ever removed tracks from the source
  group (`track_ids_remove`) and never created the singleton groups it
  said it would — every split track ended up with `group_id=None`
  (fully ungrouped), not in a new singleton group. This is the kind of
  bug a docstring-vs-implementation mismatch hides indefinitely when
  nothing ever runs the code path for real; only visible once a test
  actually asserted the split track landed *somewhere* (a new group
  with `track_count == 1`) rather than merely asserting it left the
  source group.

**Merging leaves an empty `TrackGroup` row behind — `list_groups` had
to be taught to filter it, since nothing deletes the row.**
`changes/applier.py` only ever moves `Track.group_id` pointers; no
code path deletes a `TrackGroup` once it has zero tracks. Found via an
e2e assertion that a merge should reduce the visible group count —
it didn't, because the emptied source group was still being returned
by `GET /api/groups`. Fixed by excluding `track_count == 0` groups from
`list_groups` (not by deleting the row — deleting a `TrackGroup` is a
separate, more invasive decision involving undo semantics and anything
else that might reference it by id, which this fix doesn't need to
make). Confirmed safe: the grouping cascade always sets `track_count =
len(proposal.track_ids)` whenever it creates or updates a group, so
`track_count == 0` is only ever reachable via the manual-correction
emptying path this fix targets, never as a legitimate cascade output.

### Tailwind migration (#5)

**`styles/index.css`'s `@theme` block only mapped colors/fonts/radii —
spacing and typography were never mapped at all**, despite `spacing.
css`/`typography.css` existing as real CSS custom properties. Without
mapping `--space-N`/`--text-*-size` onto Tailwind's `--spacing-*`/
`--text-*` theme keys, a class like `p-4` would have resolved against
Tailwind's own *default* spacing scale — a completely different set of
pixel values — not this design system's tokens, and the only
alternative would have been an arbitrary-value class
(`p-[var(--space-4)]`) at every one of ~350 call sites. Extended the
`@theme` block to map both scales before converting anything, naming
the Tailwind scale steps identically to `--space-N`'s own numbers (so
`--space-3` = 8px maps to `--spacing-3`, and `px-3` reliably means
"8px" everywhere in the app) — this made the actual per-file
conversion mechanical rather than needing per-site guessing.

**One real invalid-CSS bug found by inspecting generated output, not
by assuming the mapping was safe.** Mapping `effects.css`'s
`--transition-fast`/`--transition-base` (combined `"<duration>
<timing-function>"` shorthand values, e.g. `"100ms ease"`) onto
Tailwind's `ease-*` theme key generates `transition-timing-function:
100ms ease` — a duration is not a valid value for that property, so
this compiles but does nothing. Caught only because a habit formed
early in this migration (grep the actual built CSS for the generated
class after every uncertain mapping, not just trust that
`npm run build` exiting 0 means the mapping is *semantically* correct)
turned up the mismatch on the very first component that used it.
Reverted; the `transition` shorthand property stays an inline style
everywhere it's used, documented at the `@theme` site so a future
session doesn't re-attempt the same mapping.

**`--spacing`'s bare Tailwind default (`0.25rem` = 4px) happens to
equal this project's own 4px base unit, which makes numeric-multiplier
utilities like `h-16` "accidentally correct" even when they're not
going through the project's own named `--spacing-N` scale at all.**
`h-16` resolves to `16 * var(--spacing)`, and since neither this
project's `--spacing` root value nor Tailwind's default were
overridden, `16 * 4px = 64px` is right by coincidence, not because
`h-16` means anything specific to this design system the way
`px-3`/`text-sm` do. Treated as a trap, not a shortcut: every
migrated site uses either a named scale-step class (`p-4`, `gap-3`)
verified against the `--space-N` table, or an explicit arbitrary value
(`h-[64px]`) when the pixel value doesn't land on the scale — never a
bare Tailwind numeric multiplier like `h-16`/`w-24` relying on the
default-happens-to-match coincidence, since that reasoning silently
stops holding the moment anyone changes Tailwind's base `--spacing` or
adds a non-4px-multiple token to the scale later.

**Two Playwright specs located elements via raw inline-style
CSS-attribute selectors (`div[style*="width: 24px"]`) that silently
break under this migration** — 0 matches, no compile error, since
Playwright locator strings aren't typechecked against the DOM they'll
actually see. `groups.spec.ts` (`GroupDetail.tsx`'s checkbox column)
and `catalog.spec.ts` (`Catalog.tsx`'s checkbox column, three call
sites) both had this. Fixed by adding a `data-testid` to the actual
checkbox column and updating the locators — the more correct fix
regardless of this migration, and worth grepping for
`div[style*=` (or any raw inline-style locator) before converting any
page's styling, not just as a rename mechanical to this migration.
Catalog.tsx's header row has an identically-sized *placeholder* div in
the same position (no checkbox, just spacing) — deliberately left
without the `data-testid` so `.first()`/`.nth()` locators keep
resolving to real track rows, not the column header; giving both the
same testid would have been a second, more subtle way to break the
same tests.

**Colors that are always the same in a given branch convert to a
static class; colors computed from state/props stay inline — the two
are easy to conflate on a fast read.** Several early per-file batches
left `style={{ color: 'var(--diff-removed)' }}` in place even though
`--diff-removed` (and `--diff-added`/`--diff-conflict`/`--accent-text`/
`--accent-subtle-bg`) are mapped in `@theme`, because the surrounding
code *looked* similar to genuinely-dynamic sites (e.g. `Badge.tsx`'s
per-tone lookup, which correctly stays inline). A dedicated final
grep-sweep for every remaining `var(--diff-*)`/`var(--accent-text)`/
`var(--accent-subtle-bg)` usage inside a `style={{}}` — after the
per-file batches were "done" — found ~15 more sites across 10 files
that were safe to convert and had simply been missed. Worth doing this
sweep as its own final pass on any large inline-style-to-class
migration, rather than trusting that "went through every file once"
caught everything a mapped-token value could hide in.
