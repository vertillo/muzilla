# muzilla — implementation progress

Checkpoint for resuming work in a fresh session. Read `CLAUDE.md` for
conventions and `docs/PLAN.md` (local, gitignored) for full architecture.

**Last updated:** 2026-07-26
**Current phase:** Phase 5 — path templates + renaming — **in
progress**. Phase 4 (jobs + import pipeline) is fully complete; see the
git log for its details. The Phase 5 engine (`src/muzilla/paths/`, a
new package) is done and fully tested in isolation — lexer, recursive-
descent parser, AST→closure compiler, the full docs/PLAN.md §6 function
library (%upper/%lower/%title/%left/%right/%if/%ifdef/%asciify/%time/
%first/%the/%aunique/%sunique, plus muzilla's %pad/%sanitize/%default),
per-component sanitization, query-keyed template overrides
(`PathsConfig.overrides`), and batch collision detection (flat vs
foldered mode). Still remaining: `services/paths.py` (the DB-backed
`DisambiguationResolver` + batch preview/stage-rename entry points),
wiring the move-application into `changes/applier.py`'s reserved
`# rename lands in Phase 5` no-op, crash recovery for `phase="move"`
journal rows, the `/api/paths/*` router, `muzilla path-test`, and the
frontend template editor + rename preview.

No separate plan-mode document was written for this phase — `docs/PLAN.md`
§6 is detailed enough (syntax, function list, both rendering modes,
`%aunique`'s ordering trap, sanitization rules) that a second planning
pass would only restate it. A short design note lives at the top of
`/Users/asant/.claude/plans/spicy-growing-tulip.md` for the handful of
things §6 leaves open — most notably that `paths/` stays fully DB-free
(a `DisambiguationResolver` Protocol, concrete implementation in
`services/paths.py`) since PLAN.md's own layering table lists
`paths → domain` only, not `db`.

**Genuinely ambiguous point found and resolved, not stated in §6:**
when a *field value* (not the template itself) contains a literal `/`
(e.g. `albumartist="AC/DC"`), should it be silently sanitized away or
treated the same as a template-authored separator? Resolved as the
latter — the whole rendered string is checked uniformly, so a stray
`/` from field data is flagged as a validation error in flat mode (or
becomes a real directory split in foldered mode) exactly like one the
template wrote. Letting it through unflagged would be exactly the kind
of surprising, filesystem-risky behavior the "rendered `/` is a
validation error" rule exists to prevent. See
`paths/render.py`'s `render()` and its tests for the reasoning.

**Branch:** `main`, one commit per package/module:
`paths/{errors,ast,lexer}.py` → `paths/parser.py` → `paths/compiler.py`
+ `paths/context.py` → `paths/functions.py` + `paths/sanitize.py` →
`paths/query.py` + `PathsConfig` extension → `paths/render.py` →
`paths/collisions.py`. Tree is green throughout: 531 backend tests
passing (up from 386 at the end of Phase 4), 2 skipped
(fpcalc-dependent, environment-gated).

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

## Phase status

| Phase | Status |
|---|---|
| 0 — Skeleton + design system port | ✅ **Complete** |
| 1 — Read-only catalog + library analysis | ✅ **Complete** |
| 2 — Staged changes + manual editing + grouping | ✅ **Complete** |
| 3 — Providers + matching + fingerprinting | ✅ **Complete** |
| 4 — Jobs + import pipeline | ✅ **Complete** |
| 5 — Path templates + renaming | 🟨 **In progress** — engine (`paths/`) complete; services/API/CLI/applier wiring/frontend remain |
| 6 — Enrichment | ⬜ Not started |
| 7 — Hardening & release | ⬜ Not started |

---

## Commits so far

```
2a570ba fix(pipeline): run Alembic migrations automatically at startup
4c52b33 feat(web): add catalog page and login
a1f49e5 feat(api): add session auth middleware
0e91fb7 docs: update progress checkpoint after catalog/API/CLI phase-1 work
9381a24 feat(cli): add scan and analyze commands
c58bb3a feat(api): expose catalog endpoints
3453ec0 feat(services): add catalog browse and library analysis
01a2f8d feat(pipeline): add filesystem scan and track upsert
a9f2476 feat(db): add tracks and track_groups schema with FTS5 search
ed3f012 feat(tags): add mutagen-based reader across the format matrix
1b580dc feat(domain): add TrackMeta value object and ID normalization
dd730ea feat: project skeleton, design system port, and Docker packaging
```

---

## Phase 1 — remaining work

Tackle in this order; each item is one commit, tree green before moving on.

### 1. `pipeline/scan.py` — filesystem walk + probe + upsert  ✅ **Done**
- `os.scandir` walk, symlinks off by default, extension filter, default
  ignore-dir set (`.git`, `@eaDir`, recycle bins)
- Fast-path skip when `(size_bytes, mtime_ns)` unchanged — no tag read,
  no hashing
- Read tags via `muzilla.tags.reader.read_track`; on `TagReadError` sets
  `Track.probe_error` and **continues** — never aborts a scan on one bad file
- Upserts by NFC-normalized absolute `path`; marks vanished paths with
  `missing_since` rather than deleting
- Batches commits (~500 rows) to keep transactions short
- Populates `content_hash` (partial blake2b: first/last 64KB + size) and
  `tag_hash` (blake2b of the canonical `TrackMeta` tuple) — both were
  previously-unused columns now driving the Phase-2 drift check
- Committed as `feat(pipeline): add filesystem scan and track upsert`
  (`01a2f8d`); 6 new tests in `tests/pipeline/test_scan.py`. Moved
  `db_session`/`migrated_db` fixtures to the shared `tests/conftest.py`
  so non-`db/` tests can use them.

### 2. `services/catalog.py` + `services/analyze.py`  ✅ **Done**
- `catalog.py`: browse/search/detail wrapping `db.repo.tracks`, returning
  plain `TrackSummary`/`TrackDetail` dataclasses (never `db.models.Track`)
- `analyze.py`: library analysis report — album/singleton split by
  `(album, album_artist)` presence, per-field completeness against
  `domain.fields`, `(artist, title)` duplicate candidates, format/bitrate
  breakdown
- `services/db.py`: session wiring (`session_scope`/`get_session_factory`)
  since `api`/`cli` can't import `muzilla.db` at all
- Committed as `feat(services): add catalog browse and library analysis`
  (`3453ec0`)

### 3. `api/routers/tracks.py` + CLI commands  ✅ **Done**
- `GET /api/tracks` (cursor pagination, `q` FTS search, `sort`),
  `GET /api/tracks/{id}`; `api/deps.py` for per-request `Session`
- `muzilla scan <path>`, `muzilla analyze` — thin shells over
  `services/scan.py` and `services/analyze.py`
- Registered in `api/app.py` before the SPA catch-all
- **import-linter gotcha (see §Gotchas below):** had to set
  `allow_indirect_imports = "true"` on the "API and CLI may only import
  services" contract — it's transitive by default and was flagging the
  sanctioned `api → services → db` chain as a violation
- **Bug found + fixed while wiring analyze's format breakdown:**
  `Track.format` was never populated by the scan (only `codec` was, from
  mutagen's internal class name). Now derived from file extension.
- Committed as `feat(api): expose catalog endpoints` (`c58bb3a`) and
  `feat(cli): add scan and analyze commands` (`9381a24`)

### 4. Auth (plan §8)  ✅ **Done**
- `services/auth.py`: argon2 password check, signed HMAC-SHA256 session
  cookie (stdlib `hmac`/`hashlib`, not a JWT — one session at a time,
  no per-user tracking needed)
- `api/routers/auth.py`: `POST /api/auth/login`, `POST /api/auth/logout`,
  `GET /api/auth/status`, all outside `require_auth` by construction
- `api/deps.py`: `require_auth` applied per-router (to `tracks`), not
  global middleware — keeps `/api/health` and `/api/auth/login` reachable
  logged-out without a path-exclusion list
- `MUZILLA_AUTH__ENABLED=false` escape hatch preserved for local dev
- **Fail-fast startup check added:** `auth.enabled=True` (the default)
  with no password/session_secret configured now raises `RuntimeError`
  at startup instead of booting a server that 401s on everything
  including login, with no signal explaining why
- Updated `docker-compose.yml`'s auth comment
- Committed as `feat(api): add session auth middleware` (`a1f49e5`)

### 5. Frontend catalog page  ✅ **Done**
- TanStack Query client + typed API client (`lib/api.ts`, `lib/types.ts`
  mirror the backend Pydantic schemas by hand)
- Virtualized track table via `@tanstack/react-virtual` (new dep) —
  track-first, NOT album-first, per the plan's deliberate inversion
- Facets: artist/album/genre/format + flags (missing-art/no-album-tag/
  probe-errors), applied client-side over loaded pages; infinite-scroll
  pagination via the API cursor
- Login page + `AuthGuard` route wrapper, Zustand auth store
- **Bug found + fixed while verifying in-browser:** the ported
  `Checkbox` component's `onClick` lived only on the small icon box,
  not the `<label>`, so clicking the label text — the natural, larger
  click target — did nothing. Moved the handler onto the `<label>`.
- Extended `Input` with a `type` prop (text/password/search) — the
  design system port never needed password masking before now
- Verified end-to-end in headless Chromium against `muzilla serve` with
  a scanned fixture library and auth enabled: login → redirect →
  catalog renders real data (unicode titles, per-format rows) → search
  and facet toggling both work, zero console errors
- Committed as `feat(web): add catalog page and login` (`4c52b33`)

### 6. End-to-end verification  ✅ **Done**
- No real music library was available in this environment (`music/` is
  empty), so this used the committed fixture library instead — same
  scan/browse path, smaller dataset
- `docker compose up --build`: image builds, container reaches
  `healthy`, `muzilla scan` run inside the container against a mounted
  copy of the fixtures, `/api/tracks` returns real scanned data through
  a logged-in session, SPA assets and `/catalog` serve correctly
- **Found + fixed a real gap this step exists to catch:** nothing had
  ever run Alembic outside the test fixture — a fresh container 500'd
  on every `/api/tracks` call (`no such table: tracks`). Also found
  `docker-compose.yml` never actually passed `MUZILLA_AUTH__*` into the
  container, so the auth fail-fast check added earlier made a default
  `docker compose up` refuse to start. Both fixed — see gotchas below.
- Committed as `fix(pipeline): run Alembic migrations automatically at
  startup` (`2a570ba`)
- Cleaned up: removed the throwaway `muzilla_muzilla-data` Docker
  volume and test `.env` file after verification so nothing test-only
  lingers for the next real `docker compose up`

---

## What's done and verified

**Phase 0** — all acceptance criteria met and verified live:
- `docker compose up` serves `/api/health` → 200, component gallery at
  `/dev/components` → 200 with assets, container reaches `healthy`
- IBM Plex self-hosted, zero requests to fonts.googleapis.com
- 3 import-linter contracts passing

**Phase 1 so far:**
- `domain/metadata.py` — `TrackMeta` frozen dataclass mirroring
  `domain/fields.py`; `ArtworkRef`, `LyricsResult`
- `domain/ids.py` — MBID/ISRC/barcode normalization, returns `None` on
  malformed input rather than raising (tag data is untrusted)
- `tags/mapping.py` — per-format frame/key tables (Vorbis, MP4, ID3)
- `tags/reader.py` — reads all 7 formats into `TrackMeta` + probe data
- `db/models.py` — `Track`, `TrackGroup`, `SchemaMeta`
- `db/types.py` — `JSONList`, `JSONDict` column types
- `db/repo/tracks.py` — keyset-paginated browse, FTS5 search, detail
- `migrations/versions/0002_tracks_and_groups.py` — schema + FTS5 +
  sync triggers

**Test suite (end of Phase 1): 89 passing.** Format matrix (mp3/flac/
ogg/opus/m4a/wav/aiff) × common fields, probe fields, track totals,
plus corrupt/missing file handling; ID normalization; DB repo
pagination/search/JSON round-trip; scan pipeline (add/update/unchanged
fast-path/missing/corrupt-file handling); catalog + analyze services;
tracks API endpoints; scan/analyze CLI commands end-to-end via
`CliRunner`; auth (password/session-cookie unit tests + login/logout/
status/protected-route/fail-fast-startup API tests); migration runner
(schema creation, idempotency, missing-alembic.ini error).

**Phase 2 build/test details** now live in their own section below
("## Phase 2 — staged changes + manual editing + grouping").

---

## Gotchas already hit (don't re-discover these)

1. **WAV/AIFF tag dispatch.** `mutagen.File()` returns `WAVE`/`AIFF`
   objects that are NOT `ID3FileType` instances, but their `.tags`
   subclass `mutagen.id3.ID3`. Dispatch on the **tags** class, not the
   container class, or WAV/AIFF silently read as empty.

2. **FTS5 needs real migrations in tests.** `Base.metadata.create_all()`
   does not create virtual tables or triggers. Use the `db_session`
   fixture (now in the shared `tests/conftest.py`, moved from
   `tests/db/conftest.py` so non-`db/` test packages can use it too),
   which shells out to Alembic.

3. **Keyset cursor must encode the sort value, not just the id.** An
   id-only cursor skips rows whenever sort order diverges from insertion
   order. Caught by `test_list_tracks_paginates_with_cursor`.

4. **Docker: copy frontend build BEFORE `pip install`.** hatchling
   snapshots package contents at install time, so installing first ships
   an empty `web/static/`. Wheel should be ~500KB, not ~12KB.

5. **`.dockerignore` needs `!README.md`.** A blanket `*.md` exclusion
   breaks the hatchling build, which reads the readme for metadata.

6. **ffmpeg here has no `libvorbis`** — use the native `vorbis` encoder
   with `-ac 2` (it only supports 2 channels).

7. **import-linter's `forbidden` contract type is transitive by
   default.** It flags indirect imports too, not just direct ones — so
   `api → services → db` broke the "API and CLI may only import
   services" contract even though that's the exact sanctioned path the
   layers contract is built around. Fix: `allow_indirect_imports =
   "true"` on that contract. Verified the direct-import ban still works
   (a throwaway `import muzilla.db.models` inside `api/` still breaks
   it) before trusting the fix.

8. **`Track.format` looked wired but wasn't.** `db/models.py` has a
   `format` column and `tags/reader.py` sets `codec` (mutagen's class
   name, e.g. `"OggVorbis"`), but nothing ever populated `format` —
   easy to miss since nothing crashed, the field was just always
   `None`. Surfaced when `services/analyze.py`'s format breakdown came
   back empty against real scanned data. Now derived from file
   extension in `pipeline/scan.py`. **Lesson: run the CLI against the
   fixture library after wiring a report, don't just trust unit tests
   that construct `Track` rows directly** — those tests set every field
   explicitly and would never have caught this.

9. **Nothing ever ran Alembic outside the test fixture.** `muzilla
   scan`/`serve` both assumed an already-migrated DB; a fresh
   `docker compose up --build` produced a container that passed
   `/api/health` but 500'd on every `/api/tracks` call (`no such table:
   tracks`) — SQLite happily creates an empty file on connect, so
   nothing failed loudly until a query actually ran. Fixed by running
   `alembic upgrade head` as a subprocess (`services/migrate.py`) from
   the API lifespan and the scan/analyze CLI commands, reusing the
   `MUZILLA_ALEMBIC_DB_PATH` convention the test fixture already used.
   Only findable by actually running `docker compose up --build` and
   hitting the API — unit/integration tests all build their DB through
   the `migrated_db` fixture, which bypasses this exact gap by design.

10. **`docker-compose.yml`'s `--env-file` doesn't reach the
    container.** `--env-file`/`env_file:` only populate `${...}`
    interpolation inside the compose file itself — they don't become
    the container's runtime environment unless something in the
    compose file actually references them (e.g. an `environment:`
    block). Adding the auth fail-fast check made this concrete: a
    default `docker compose up` with a password set only via
    `--env-file` still refused to start, because the container never
    saw `MUZILLA_AUTH__PASSWORD` at all. Fixed with an explicit
    `environment:` block; added `.env.example` (and a `.gitignore`
    exception, since `.env.*` was blanket-ignored) documenting the two
    required vars.

---

## Phase 2 — staged changes + manual editing + grouping

**First phase that writes to disk.** Deliberately sequenced last of the
three "safe" phases so the changeset/journal/undo machinery exists
*before* the first write — see docs/PLAN.md's framing of this as the
central risk-mitigation decision of the whole project.

### What was built

**`db/models.py` + `migrations/versions/0003_changes.py`**
- `ChangeSet`, `Change`, `ApplyJournal`, `Blob` tables per docs/PLAN.md
  §5's exact column specs (state machines, `candidate_source`/
  `candidate_ref`, `is_manual`, `before_blob` JSON, sharded
  `storage_path`, `refcount`).
- Used a plain integer PK for `change_sets` rather than the plan's
  UUID7 suggestion — SQLite has no native UUID type and there's no
  cross-node id-generation need in a single-container, single-writer
  app. Documented as a deliberate, revisitable deviation in the model
  docstring.
- `Change.old_value`/`new_value` are `sa.JSON`, not the existing
  `JSONDict`/`JSONList` types — a Change's value can be a string, a
  list of strings, a number, or a bool depending on the field's
  `FieldType`, so a fixed-shape column type doesn't fit.

**`src/muzilla/tags/writer.py`** — mutagen-based tag writer mirroring
`reader.py`'s per-format dispatch (Vorbis/MP4/ID3, dispatching on the
`.tags` class exactly like the reader does per gotcha #1). Verified to
round-trip cleanly against all 7 fixture formats for every field
category exercised by the test suite (title, comment-clear, track_no,
multi-valued genre, boolean compilation, provider-id fields).

**`src/muzilla/changes/`** — the core package:
- `differ.py` — `FieldDiff` computation: char-level inline diff via
  `difflib.SequenceMatcher` opcodes (split "replace" into paired
  delete/insert spans so old/new each get a flat span list), ordered-
  set diffing for multi-valued fields, `kind="binary"` shape for the
  art pseudo-field (no art pipeline exists yet, but the shape is
  ready), and a destructive-severity rule (clearing a populated field,
  or any `move` op, is destructive).
- `blobstore.py` — content-addressed (sha256), two-level sharded
  on-disk store with refcount-based `retain()`/`release()`. `put()`
  itself never bumps refcount — callers must explicitly retain once a
  blob is attached somewhere, so an uploaded-but-never-referenced blob
  doesn't leak a phantom reference.
- `conflicts.py` — `probe()`: re-reads a file and recomputes
  `tag_hash`, comparing against the hash recorded at stage time. A
  `None` baseline (pre-Phase-2 scanned tracks) never conflicts.
- `builder.py` — `build_changeset()`: the single construction path for
  every changeset source (`manual_edit`, `strip_tags`,
  `grouping_correction`, `undo_of:<id>`); `match_proposal`/`rename` are
  accepted by the source-name validator (columns/enum values exist)
  but nothing produces them yet — no Phase 3 provider or Phase 5 path
  code. Per-kind default decisions live in
  `default_decision_for_kind()` (strip auto-accepts default-strip
  fields; grouping corrections auto-accept since the user explicitly
  requested the action).
- `applier.py` — the highest-risk module, implementing the exact
  6-step apply spec: probe/conflict-check, journal `PENDING` with
  `before_blob`, same-directory tmp-file write + fsync, `os.replace`,
  journal `DONE` with `after_hash`. `abort_all`-style compensation is
  *not* implemented as a separate pass in Phase 2 — a per-file failure
  simply stops that file's write and marks its Changes
  `conflicted`/`failed` without touching files already written by
  earlier iterations in the same apply call, which is safe because
  each file's write is already atomic; multi-file compensating rollback
  and startup crash-recovery scanning are Phase 4 job-table scope (the
  journal rows this phase writes are exactly what that recovery will
  read).
- `undo.py` — synthesizes an inverse `ChangeSet` (`source="undo_of:<id>"`)
  from an applied changeset's `apply_state="applied"` Changes, re-run
  through the identical `applier.py` path. Undo changes are
  pre-accepted (the user already approved the original edit and is
  explicitly reverting it).

**`src/muzilla/domain/normalize.py`** — the minimal slice of
docs/PLAN.md §3's `string_dist.py` spec that Phase 2's grouping cascade
actually needs: NFKD/strip-combining/casefold, leading-article
stripping, `feat.` canonicalization, punctuation normalization, plus
`string_dist()` combining rapidfuzz's `token_set_ratio` and `ratio`.
Roman-numeral unification and curated noise-regex bracket stripping are
explicitly left for Phase 3's full matching engine (the docstring notes
this so the module isn't mistaken for the complete spec).
Also added `domain/metadata.tag_hash()` — hoisted out of
`pipeline/scan.py`'s previously-private `_tag_hash` so both scan-time
and apply-time (`changes/conflicts.py`) compute the identical hash
without `changes` needing to import `pipeline` (which sits above it in
the layering contract). `pipeline/scan.py` now delegates to it.

**`src/muzilla/pipeline/grouping.py`** — the confidence-scored cascade,
Stages 1/3/4 (Stage 2 fingerprint consensus is explicitly Phase 3, no
AcoustID data exists yet):
- Stage 1: exact-match clustering by `mb_release_id`, then `barcode`,
  then `(catalog_number, label)`, confidence 1.0.
- Stage 3: fuzzy single-link clustering on `(album_artist, album)` via
  `domain.normalize.string_dist` with a distance threshold rather than
  exact equality, confidence scaled 0.5–0.85 by intra-cluster
  tightness.
- Stage 4: singleton classification (no album tag, album==title,
  `track_total==1`, or survives 1+3 as a cluster of one).
- Partial-album detection: flags `kind="partial_album"` when every
  track in a cluster agrees on a tag-level `track_total` larger than
  the cluster itself — the one signal available before Phase 3 match
  results exist. Verified live against the real Sigur Rós fixture
  files (`track_total=10` tag, 2 files present) — correctly flagged
  partial rather than silently guessed either way.
- Pinned groups (`TrackGroup.is_pinned`) are completely excluded from
  re-clustering, and re-running the cascade never overwrites a pinned
  group's fields — verified by test.

**Services layer** (`src/muzilla/services/`):
- `changesets.py` — list/get/patch-decisions/apply/undo, returning
  plain dataclasses (`ChangeSetDetail`, `ChangeOut` with an embedded
  `FieldDiff`) never `db.models` rows.
- `edit.py` — single-track edit, bulk edit (explicit
  `BulkEditField` list so an unedited field is never touched, avoiding
  the classic bulk-editor trap of flattening distinct values), and
  find-and-replace preview/apply with regex opt-in.
- `strip.py` — wires `domain.fields.default_strip_fields()` into a
  `strip_tags` ChangeSet, skipping fields already empty on a track.
- `grouping.py` — `run_cascade`, `list_groups` (confidence-ascending —
  worst first), `merge_groups`/`split_group`/`reassign_track`/
  `force_to_singleton`/`pin_group`, every one of which builds and
  returns a `ChangeSet` via the same `changes/builder.py` used by
  manual editing — "a grouping correction is itself a ChangeSet."
- `fields.py` — exposes `domain.fields.FIELDS` as plain `FieldInfo`
  dataclasses so the frontend never hardcodes a duplicate field list.

**API** (`src/muzilla/api/`):
- `routers/changesets.py` — `GET/PATCH /api/changesets/{id}`,
  `PATCH .../changes`, `POST .../apply`, `POST .../undo`, plus
  `PATCH /api/tracks/{id}` (creates a DRAFT ChangeSet, never writes
  directly), `POST /api/tracks/bulk-edit`,
  `/api/tracks/find-replace(/preview)`, `/api/tracks/strip`.
- `routers/groups.py` — `GET /api/groups`, `GET /api/groups/{id}`,
  `POST /api/groups/cascade`, and the correction actions
  (merge/split/reassign/force-singleton/pin).
- `routers/fields.py` — `GET /api/fields`.
- `idempotency.py` — minimal `Idempotency-Key` support (docs/PLAN.md
  §10) via an in-process cache on `app.state`, deliberately not a DB
  table: this is a single-container app with no multi-process story
  yet, so a process-local cache is an acceptable Phase 2 tradeoff
  (documented in the module as revisitable once Phase 4's job table
  formalizes cross-restart state). Applied to `apply`/`undo` — the two
  endpoints where a client retry after a dropped response must not
  double-apply or spawn a second undo changeset.
- `changes/differ.py` gained a fallback for unregistered field names:
  grouping-correction pseudo-fields (`track_ids_add`, `is_pinned`, …)
  aren't in `domain.fields` (that registry is track-tag-scoped), so
  `diff_field` renders them as a generic scalar diff instead of
  `KeyError`ing — caught by an API-level integration test, not a unit
  test (see gotchas below).

**CLI** (`src/muzilla/cli/commands/`):
- `edit.py` — `muzilla edit <track_id> -f field=value` (repeatable;
  empty value clears the field), staging a DRAFT ChangeSet.
- `changes.py` — `muzilla changes show/apply/undo/list`, matching the
  plan's end-to-end smoke-test shape (`muzilla changes apply
  <changeset-id>`, `muzilla changes undo <changeset-id>`) exactly.

**Frontend** (`frontend/src/`):
- `lib/types.ts` / `lib/api.ts` extended with every Phase 2 schema/
  endpoint, hand-mirrored field-for-field per the existing convention.
  `applyChangeset`/`undoChangeset` send a client-generated
  `Idempotency-Key` (`crypto.randomUUID()`) automatically.
- `pages/ChangeSetReview.tsx` — the three-pane diff review screen: left
  pane entity list (collapses automatically for a single-entity
  changeset — singleton mode, per docs/PLAN.md §9), center pane
  field-level diff rows (inline char-diff via a ported `InlineDiff`
  component, multi-valued ordered-set badges, binary-field summary
  rendering, `ThreeStateToggle` reconciled to
  `pending|accepted|rejected` with `is_manual` shown as a separate
  badge rather than inventing a fourth toggle position), right pane
  **stubbed as a clear `EmptyState`** explaining candidates need Phase
  3 providers — not faked. Bulk actions (accept all / accept
  non-destructive / accept field across all tracks) and keyboard
  shortcuts (`j/k` navigate, `a/r` accept/reject, `A` accept-all, `e`
  edit, `Enter` apply, all guarded against `INPUT`/`TEXTAREA` focus)
  per §9.
- `pages/TagEditor.tsx` — single + bulk manual editor over
  `GET /api/fields`, grouped by `FieldCategory`; bulk mode shows
  `<multiple values>` placeholders for fields that differ across the
  selection and only submits fields the user actually touched. Includes
  find-and-replace with a live preview panel and regex opt-in.
- `pages/Groups.tsx` / `pages/GroupDetail.tsx` — the grouping workspace
  (sorted worst-confidence-first) and a per-group detail view with
  pin/split/force-to-singleton actions plus an in-page merge flow.
- `pages/ChangesList.tsx` — `/changes` list with state filter and undo.
- `pages/Catalog.tsx` — gained row selection (checkboxes + a bulk-edit
  action bar navigating to `/edit?ids=...`) and a title-click shortcut
  into the single-track editor; this was the minimum viable selection
  UI needed to make the bulk editor reachable, not a full redesign.
- `App.tsx` — wired `/edit`, `/groups`, `/groups/:id`, `/changes`,
  `/changes/:id` behind `AuthGuard`, matching docs/PLAN.md §9's route
  table.

### Verification

- Backend: **168 tests passing** (up from 89 at end of Phase 1), plus
  `ruff check src tests`, `mypy src` (strict), and `lint-imports` (3/3
  contracts kept) all clean. New test packages: `tests/changes/`
  (differ, blobstore, apply/undo end-to-end against real fixture
  files — including a genuine conflict-detection test that mutates a
  file between stage and apply), `tests/pipeline/test_grouping.py`,
  `tests/domain/test_normalize.py`, plus new coverage under
  `tests/services/` (edit, strip, grouping, changesets) and
  `tests/api/` (changesets, groups) and `tests/cli/`
  (edit_and_changes).
- Frontend: `npm run lint` (oxlint), `npm run typecheck`, and
  `npm run build` all clean.
- **Live smoke test beyond the automated suite**: scanned a small real
  fixture library, ran `muzilla edit` to stage a title change, accepted
  it, ran `muzilla changes apply` — confirmed the on-disk MP3's ID3
  title frame actually changed via a fresh `read_track()` call — then
  ran `muzilla changes undo` and confirmed the file's title reverted to
  byte-identical original content. Also exercised `POST /api/groups/
  cascade` against the same library via `TestClient`, which correctly
  flagged a `partial_album` (fixture tags claim `track_total=10`, only
  2 files present) rather than guessing.

### Gotchas discovered this phase

11. **`session.add(child)` + setting a FK column directly leaves the
    parent's in-session relationship collection stale.** `changes/
    builder.py` originally did `Change(change_set_id=change_set.id, ...)`
    + `session.add(change)`. With `expire_on_commit=False` (this
    project's session factory setting), `change_set.changes` is never
    refreshed after that — the collection was lazy-loaded once (empty,
    before any Changes existed) and nothing invalidates it, so every
    caller that read `change_set.changes` immediately after
    `build_changeset()` (without a full session expire/refresh) saw an
    empty list. Symptom was silent: `apply_changeset()`'s fresh
    `select(Change).where(...)` query was unaffected (it reads straight
    from the DB), but anything relying on the in-memory relationship —
    including the first draft of several tests — got `[]` and
    `ValueError: no applied changes to undo` two calls later. Fixed by
    appending through the relationship (`change_set.changes.append(change)`)
    instead of setting the FK directly; SQLAlchemy keeps the collection
    and the FK in sync automatically that way. Caught immediately by
    `tests/changes/test_apply_undo.py`'s conflict/undo tests — worth
    remembering because every future package that builds one-to-many
    rows inside a single session before commit needs the same care.
12. **`tags/reader.py`'s `_read_mp4` compilation field was a latent
    crash, not just a stylistic gap.** `tags.get(MP4_STANDARD_KEYS
    ["compilation"], [False])[0]` assumed `cpil` is stored as a
    one-element list; mutagen actually stores MP4 `cpil` as a scalar
    Python `bool`. The fixture never sets `cpil`, so `.get()` always hit
    the `[False]` default and nothing ever tried to subscript a real
    value — the bug was invisible until `tags/writer.py`'s round-trip
    test actually wrote `compilation=True` and the next `read_track()`
    call raised `TypeError: 'bool' object is not subscriptable`. Fixed
    in `reader.py` (default changed to plain `False`, no list). Same
    lesson as gotcha #8 from Phase 1: a field that "looks wired" but has
    never been exercised with a real non-default value can hide a crash
    indefinitely.

13. **`VComment.pop(key, default)` (Vorbis comments) doesn't accept a
    default arg**, unlike a normal dict — raises `TypeError: pop
    expected at most 1 argument, got 2`. `tags/writer.py`'s Vorbis path
    uses a small `_vc_del()` helper (`if key in tags: del tags[key]`)
    instead. MP4Tags, by contrast, *is* a real dict subclass and
    `.pop(key, None)` works fine there — the two mutagen tag containers
    are not API-uniform despite looking similar.

14. **`differ.diff_field()` assumed every field name is in the
    `domain.fields` registry — group-entity Changes break that
    assumption.** Grouping corrections stage pseudo-fields
    (`track_ids_add`, `track_ids_remove`, `is_pinned`) that describe
    `TrackGroup` mutations, not track tags, so they were never meant to
    be in `domain.fields` (which is explicitly the tag-field registry).
    `field_registry.get()` raising `KeyError` on those names wasn't
    caught by any `changes/`-level unit test (which only ever exercised
    track-entity diffs) — it only surfaced as a 500 from
    `tests/api/test_groups.py`'s `pin`/`force-singleton` endpoint tests,
    which go through the full `services.changesets.get_changeset()` ->
    `_diff_for_change()` path. Fixed with a documented fallback: an
    unregistered field name renders as a generic `kind="scalar"` diff
    with a title-cased label instead of crashing. Lesson repeats gotcha
    #8's pattern from Phase 1: integration tests that exercise a real
    end-to-end path catch what field-scoped unit tests structurally
    cannot.

---

## Known gaps / deliberate deferrals

- **Phase 2 scope boundaries respected, not blurred**: no provider/
  matching/fingerprinting code exists (`match_proposal` accepted as a
  valid `ChangeSet.source` value but nothing produces it); no path-
  template/rename logic (`op="move"` is modeled in the schema and
  the differ but the applier explicitly no-ops on it, deferring to
  Phase 5); no jobs table or async worker (apply/undo run synchronously
  inline, which is correct for Phase 2's request-response flows but
  will need to move behind the job queue once bulk operations at
  library scale arrive in Phase 4).
- **The apply path's crash recovery is per-file-atomic, not yet
  library-wide-atomic.** Each file's write is journaled and safe
  individually (tmp-file + `os.replace`), but there is no startup scan
  that inspects `PENDING`/`WRITING` journal rows left by a mid-apply
  process crash and restores them from `before_blob` — docs/PLAN.md
  explicitly scopes that recovery pass to Phase 4 alongside the job
  table, and this phase's journal rows are exactly the data that
  recovery pass will consume.
- **The candidate-picker right pane of `ChangeSetReview.tsx` is an
  intentional `EmptyState` stub**, not a placeholder pretending to be
  real — there is no provider/matching code yet to populate it, per
  the task's explicit instruction not to fake Phase 3 UI.
- **No blob is ever actually created by anything yet.** `changes/
  blobstore.py` is fully implemented and tested in isolation, but
  nothing calls `put()` in the apply/undo path — `before_blob` on
  `ApplyJournal` currently stores only the small JSON tag payload (as
  the plan specifies: "<5KB; art in blob store"), and art itself has no
  producer until Phase 6's embed/resize pipeline exists.
- **`db/models.py` is now Phase-2-scoped.** `jobs`, `job_events`,
  `import_sessions`/`import_tasks`, `settings`, `users` still come in
  Phase 4+. (`provider_cache` and `track_fingerprint_matches` landed in
  Phase 3 — see below.)

---

## Phase 3 — providers + matching + fingerprinting

**Fully complete, backend and frontend.** Committed as fourteen focused
commits (one per package/layer, including the two docs checkpoints),
each independently green — see `git log --oneline` for the exact
sequence. 111 new backend tests this phase (198 → 299), plus the
frontend candidate picker (see "Frontend candidate-picker UI" below),
verified live against real MusicBrainz/Deezer network calls.

### What was built

**`src/muzilla/matching/`** — the pure, network-free scoring core:
- `distance.py` — `weighted_distance()` (Σw·d/Σw), plus
  `numeric_distance`/`exact_distance` helpers. Callers must *omit* a
  field key entirely when there's no local data for it (a missing
  barcode, no per-track artist credit) rather than scoring it as a
  mismatch — `weighted_distance` only excludes a field from its
  denominator when the key is absent from the dict it's given.
- `weights.py` — `ALBUM_WEIGHTS`/`TRACK_WEIGHTS`/`SINGLETON_WEIGHTS`
  and the auto-apply/confirm thresholds (album 0.10/0.25, singleton
  0.06), verbatim from docs/PLAN.md §3.
- `track_align.py` — Hungarian algorithm (`scipy.optimize.
  linear_sum_assignment`) track alignment with a sequential-order
  tie-breaking prior and an optional disc-aware split. Generic over
  local/candidate track types (PEP 695 type params).
- `candidates.py` — `gather_candidates()` fans out to every enabled
  provider in parallel (`asyncio.gather(return_exceptions=True)` — a
  dead provider never fails the match); `rank_candidates()` scores,
  flags duplicate-alternative candidates (shared barcode/MBID, or
  close text distance + matching track count + `|year|<=1`), applies
  a corroboration bonus and source-priority tie-break penalty, then
  sorts. Never merges fields across candidates.
- `engine.py` — the two entry points: `propose_for_group()` (release-
  level, Hungarian alignment, `ALBUM_WEIGHTS`) and
  `propose_for_singleton()` (recording-level, stricter threshold,
  earliest-release preference). Kept as two separate functions, not
  one with a branch — see the module docstring.

**`src/muzilla/providers/`** — protocols, rate limiting, caching, and
five concrete clients:
- `base.py` — four narrow Protocols (`MetadataProvider`, `ArtProvider`,
  `LyricsProvider`, `FingerprintProvider`) and the normalized
  `ReleaseQuery`/`ReleaseCandidate`/`ProviderRef`/`CandidateTrack`
  shapes. A `ReleaseCandidate` always carries the one provider it came
  from — the "one release, one source" rule enforced at the type level.
- `ratelimit.py` — process-global async token buckets per provider
  (shared between interactive and background callers), plus a
  hard-serializing lock for MusicBrainz specifically (its server-side
  limiter 503s on overlapping requests even at nominal 1 req/s).
- `cache.py` — two deliberately separate layers: an `hishel`-wrapped
  httpx client (HTTP-level ETag/Cache-Control caching) and a semantic
  cache (new `provider_cache` table, keyed `(provider, operation,
  query_hash)`) storing *normalized* candidates so matching can re-run
  offline against already-fetched data.
- `musicbrainz.py`, `deezer.py`, `discogs.py`, `coverartarchive.py`,
  `lrclib.py` — built by a background subagent against the Protocols
  above, then reviewed line-by-line before trusting it (see the
  gotchas below for the one real environment issue it hit). Discogs
  degrades gracefully with no token configured (docs/PLAN.md §8) rather
  than crashing or being constructed broken.
- `acoustid.py` — talks to `api.acoustid.org/v2/lookup` directly over
  the shared rate-limited httpx client, deliberately *not* reusing
  `pyacoustid`'s built-in `lookup()` (which uses sync `requests` and
  would bypass rate limiting/caching entirely).

**`src/muzilla/audio/fingerprint.py`** — sync `compute_fingerprint()`
wrapping `pyacoustid`'s fpcalc dispatch. Sync and living in `audio/`
rather than `providers/` because fpcalc is a CPU-bound subprocess, not
network I/O — belongs behind a bounded thread/process pool the Phase 4
scan pipeline will manage, not called directly from async code.

**`src/muzilla/pipeline/grouping.py`** — Stage 2 (fingerprint
consensus), the last piece of the grouping cascade deferred from Phase
2. Groups tracks whose AcoustID lookups independently agree on the
same release MBID (>=3 tracks or >=50% of fingerprinted tracks),
confidence 0.9. Reads from the new `track_fingerprint_matches` table
(one row per (track, candidate recording) — a lookup can return several
plausible recordings) rather than calling AcoustID itself, keeping the
cascade a pure read like Stages 1/3/4.

**`src/muzilla/services/`**:
- `providers.py` — `build_provider_set(config)` turns a `Config` into
  live provider clients, one httpx client per provider built once at
  process startup (not per-request). Auth-required providers with no
  token are simply absent from the built set.
- `matching.py` — the DB<->engine seam: `propose_group_candidates`/
  `propose_track_candidates` (read-only fetch+rank) and
  `stage_group_match`/`stage_track_match` (re-fetch the chosen release,
  align tracks, build a `match_proposal` ChangeSet via the existing
  `changes/builder.py`). Re-picking a candidate is just calling
  `stage_*` again — always rebuilds from scratch, never merges with a
  prior proposal.

**API + CLI** — `GET/POST /api/groups/{id}/candidates|stage`,
`GET/POST /api/tracks/{id}/candidates|stage` (docs/PLAN.md §10), and
`muzilla match group|track <id> [--stage source:ref_id]` calling the
exact same service functions as the API.

**Scoring regression corpus** (`tests/fixtures/matching/{album,
singleton}/*.yaml` + `tests/matching/test_scoring_corpus.py`) — 13
real-world scenarios (diacritics/dropped-article damage, remastered-
suffix noise, out-of-order rips, duplicate-source flagging, label/
catalog tiebreaks, roman-numeral titles, adversarial wrong-release
rejection, earliest-release singleton preference, ISRC corroboration)
run as a top-1 accuracy regression. Per docs/PLAN.md: "the highest-value
test asset in the project" — extend it whenever a real mismatch is found.

### Real bugs/gaps caught while building this (worth knowing about)

1. **Roman-numeral unification false-positive.** A naive `\b(roman-
   numeral-shaped-token)\b` regex converted ordinary English words
   spellable from roman-numeral letters — "Mix" (M+IX) became "1009",
   "Civic" and "Live" were also at risk. Fixed by scoping the
   conversion to directly after a recognized ordinal marker ("Pt.",
   "Vol.", "No.", "Disc", ...) instead of matching anywhere in a
   string. Caught by a test, not by inspection.
2. **Missing-field distance bug.** `_track_pair_distance`,
   `_album_candidate_distance`, and `_singleton_candidate_distance` all
   originally scored "no local data for this field" (no per-track
   artist credit, no barcode, no ISRC — common on sparsely-tagged
   files) as a full 1.0 mismatch instead of omitting the field from
   `weighted_distance`'s denominator. This would have systematically
   inflated distance for exactly the era-varying, sparsely-tagged files
   this whole library shape is full of. Caught by the engine test suite
   before ever touching provider data.
3. **hishel API version mismatch.** The plan's `>=0.0.33` constraint
   and documented `FileStorage`/`Controller`/`AsyncCacheTransport` API
   only exist pre-1.0; the latest release (1.3.0) is a ground-up
   rewrite with a completely different API surface
   (`AsyncCacheProxy`/`AsyncSQLiteStorage`, no `FileStorage`). Pinned to
   `hishel>=0.1.0,<1.0` to match the documented, stable API rather than
   reverse-engineering the 1.x rewrite for something this peripheral.
4. **SQLite `DateTime(timezone=True)` doesn't reliably round-trip
   tzinfo.** `provider_cache`'s expiry check (`row.expires_at <=
   datetime.now(UTC)`) raised `TypeError: can't compare offset-naive
   and offset-aware datetimes` on the very first real read — SQLite
   returns a naive datetime even though the column is declared
   timezone-aware. Fixed by normalizing to UTC on read before comparing.
5. **`build_provider_set` running at every app startup breaks every
   existing API test.** Wiring `ProviderSet` construction into
   `api/app.py`'s lifespan means every test that boots the app (not
   just new matching tests) now creates on-disk HTTP cache directories
   under `MUZILLA_STORAGE__CACHE_DIR`, whose packaged default (`/data`)
   is only writable inside the Docker image. Every existing test using
   the shared `client` fixture, plus `test_auth.py`'s separate
   `auth_client` fixture (doesn't reuse the shared one), needed
   `MUZILLA_STORAGE__CACHE_DIR` pointed at `tmp_path`. Found by running
   the *whole* suite after adding the endpoints, not just the new tests
   — a lesson that repeats Phase 1/2's "run against real data/paths,
   don't trust unit tests in isolation."
6. **A scoring-corpus fixture that looked like a bug wasn't one.**
   A 2-of-4-tracks-present album scenario scored distance 0.056
   (auto-applicable) even with most of the release's tracks absent
   locally. Initially looked like a `missing_tracks` weight
   miscalibration worth "fixing." Discussed with the user first:
   muzilla is a metadata tool, not a rip-completeness verifier —
   "I only ever wanted these 2 songs" is legitimate and common, and
   docs/PLAN.md §7b already assigns the incomplete-rip-vs-deliberate-
   subset judgment call to the *grouping* cascade's `partial_album`
   flag, not to matching (whose only job is "is this the right
   release"). Fixed the *fixture's expectation*, not the weights —
   documented inline so a future weight-tuning pass doesn't "fix" this
   by accident.

### Frontend candidate-picker UI (completed in a follow-up session)

`ChangeSetReview.tsx`'s right pane (previously an intentional
`EmptyState` stub from Phase 2, deliberately deferred past the initial
Phase 3 backend push so backend quality wasn't rushed near a session
boundary) is now a real release-level candidate picker:

- `frontend/src/components/CandidatePicker.tsx` — ports the Change
  Review Claude Design prototype's visual language (provenance-toned
  `Badge` with `dot`, `ConfidenceBar`, a bordered/tinted card for the
  selected row) but restructured around docs/PLAN.md §9's explicit
  correction: one card per `(source, release)`, never per field. No
  `winnerOverrides`, no per-field "use this," no per-row source-vs-
  source comparison. Picking a card calls the existing
  `POST .../stage` endpoint and re-stages the *entire* changeset.
  Shows duplicate-alternative and corroboration hints (visual only)
  and the auto-applicable/needs-confirmation banner.
- `frontend/src/hooks/useMatching.ts` + `lib/api.ts`/`lib/types.ts`
  additions — `useCandidates` (read-only) / `useStageMatch`, and
  `CandidateRow`/`MatchProposal` mirroring `api/schemas/matching.py`.
- `ChangeSetReview.tsx` wires the picker in using `scope_type`/
  `scope_id` (already on `ChangeSetDetail`) to route to the group- or
  track-scoped endpoint — no new backend fields were needed.

**Verified live**, not just typechecked: no project `run` skill existed
for this app yet, and `chromium-cli` wasn't available in this
environment, so verification used a bare Playwright script driving a
real `muzilla serve` process (SQLite + hishel caches under a scratch
dir, auth disabled) — against **real MusicBrainz/Deezer network
calls**, not mocked. Confirmed: the grouping cascade → real ranked
candidates (7 rows) → staging → picker renders correctly in both
themes with zero console errors → clicking "Use this" on an alternate
release creates a *new* changeset (never mutates the existing one) with
the diff recomputed from that release's real tags and the `CURRENT`
badge moved correctly → singleton (track-scoped) mode separately
verified with 10 real recording-level candidates, the auto-applicable
banner at 96% confidence, and the left pane correctly collapsed for
the single-entity case.

**Consider generating a project `run` skill** (`/run-skill-generator`)
next time this app needs live verification — the dev-server-launch +
Playwright-driver steps above (env vars, port, scan-then-cascade
sequence to get a real group to test against) had to be rediscovered
from scratch this session and would be cheap to capture.

### Known remaining gaps (not blocking Phase 3, revisit later)

- `PUT /api/changesets/{id}/candidate` (re-stage an *existing* draft
  changeset from a different release, per docs/PLAN.md §10's exact
  endpoint name) is not a separate endpoint — re-staging today means
  calling `POST .../stage` again, which creates a **new** changeset
  rather than mutating the existing one in place. This is confirmed,
  intentional current behavior (see the live-verification note above),
  but doesn't match the plan's literal `PUT .../candidate` semantics.
  Worth revisiting if the UX of "replace this draft in place" turns
  out to matter more than "each candidate pick is its own reviewable
  changeset."
- Contract-tier (scheduled, `@pytest.mark.network`) provider tests are
  explicitly out of scope per docs/PLAN.md's testing strategy (tier 3,
  weekly CI only) — none were added. The live Playwright verification
  above hit real network once, manually, which is different from an
  automated recurring contract-test suite.
