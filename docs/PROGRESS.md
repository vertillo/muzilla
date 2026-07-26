# muzilla — implementation progress

Checkpoint for resuming work in a fresh session. Read `CLAUDE.md` for
conventions and `docs/PLAN.md` (local, gitignored) for full architecture.

**Last updated:** 2026-07-26
**Current phase:** Phase 1 complete — resume with Phase 2 (staged
changes + manual editing + grouping) per `docs/PLAN.md`
**Branch:** `main` — working tree clean, all committed

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
| 2 — Staged changes + manual editing + grouping | ⬜ Not started |
| 3 — Providers + matching + fingerprinting | ⬜ Not started |
| 4 — Jobs + import pipeline | ⬜ Not started |
| 5 — Path templates + renaming | ⬜ Not started |
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

**Test suite: 89 passing** (backend; run `npm run lint && npm run
typecheck && npm run build` separately for the frontend, which has no
test runner configured yet). Format matrix (mp3/flac/ogg/opus/m4a/wav/
aiff) × common fields, probe fields, track totals, plus corrupt/missing
file handling; ID normalization; DB repo pagination/search/JSON
round-trip; scan pipeline (add/update/unchanged fast-path/missing/
corrupt-file handling); catalog + analyze services; tracks API
endpoints; scan/analyze CLI commands end-to-end via `CliRunner`; auth
(password/session-cookie unit tests + login/logout/status/protected-
route/fail-fast-startup API tests); migration runner (schema creation,
idempotency, missing-alembic.ini error).

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

## Known gaps / deliberate deferrals

- **`db/models.py` is Phase-1-scoped.** `change_sets`, `changes`,
  `apply_journal`, `blobs`, `jobs`, `provider_cache` come in Phase 2+.
- **`Track.tag_hash` and `content_hash` are now populated** by the scan
  (blake2b tag-tuple hash and partial content hash respectively) but
  **nothing consumes them yet** — they become the drift-detection check
  once writes exist in Phase 2.
- **No writes anywhere yet**, by design. Phase 1 is safe to point at a
  real library.
