# muzilla — implementation progress

Checkpoint for resuming work in a fresh session. Read `CLAUDE.md` for
conventions and `docs/PLAN.md` (local, gitignored) for full architecture.

**Last updated:** 2026-07-26
**Current phase:** Phase 1 — Read-only catalog + library analysis
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
| 1 — Read-only catalog + library analysis | 🔨 **In progress (~40%)** |
| 2 — Staged changes + manual editing + grouping | ⬜ Not started |
| 3 — Providers + matching + fingerprinting | ⬜ Not started |
| 4 — Jobs + import pipeline | ⬜ Not started |
| 5 — Path templates + renaming | ⬜ Not started |
| 6 — Enrichment | ⬜ Not started |
| 7 — Hardening & release | ⬜ Not started |

---

## Commits so far

```
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

### 2. `services/catalog.py` + `services/analyze.py`  ⬅ **NEXT**
- `catalog.py`: browse/search/detail wrapping `db.repo.tracks`. This is
  the ONLY layer `api/` and `cli/` may call.
- `analyze.py`: the library analysis report — actual album/single split,
  tag completeness per field, how many files have usable album tags,
  duplicate candidates, format/bitrate breakdown. Cheap on top of scan
  and genuinely informative for a flat, mixed-vintage library.
- Commit as `feat(services): add catalog browse and library analysis`

### 3. `api/routers/tracks.py` + CLI commands
- `GET /api/tracks` (cursor pagination, `q` FTS search, `sort`),
  `GET /api/tracks/{id}`
- `muzilla scan <path>`, `muzilla analyze`
- Register routers in `api/app.py` **before** the SPA catch-all
- Commit as `feat(api): expose catalog endpoints` and
  `feat(cli): add scan and analyze commands`

### 4. Auth (plan §8)
- argon2 password hash from `MUZILLA_AUTH__PASSWORD`, signed HttpOnly
  cookie session, `MUZILLA_AUTH__ENABLED=false` escape hatch
- Config schema already exists in `config/schema.py` (`AuthConfig`) but
  **nothing enforces it yet** — no middleware, no login route
- `docker-compose.yml` currently has a comment noting auth isn't
  enforced; update it once this lands
- Commit as `feat(api): add session auth middleware`

### 5. Frontend catalog page
- TanStack Query client + typed API client
- Virtualized track table (TanStack Table) — track-first, NOT album-first
- Facets: artist/album/genre/year/format/missing-art/unmatched
- Login page + auth guard
- Design system primitives already exist in `frontend/src/components/ui/`
- Commit as `feat(web): add catalog page and login`

### 6. End-to-end verification
- `muzilla scan` a copy of real music, browse via CLI and browser
- `docker compose up --build` and confirm the whole flow in the container

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

**Test suite: 47 passing.** Format matrix (mp3/flac/ogg/opus/m4a/wav/aiff)
× common fields, probe fields, track totals, plus corrupt/missing file
handling; ID normalization; DB repo pagination/search/JSON round-trip.

---

## Gotchas already hit (don't re-discover these)

1. **WAV/AIFF tag dispatch.** `mutagen.File()` returns `WAVE`/`AIFF`
   objects that are NOT `ID3FileType` instances, but their `.tags`
   subclass `mutagen.id3.ID3`. Dispatch on the **tags** class, not the
   container class, or WAV/AIFF silently read as empty.

2. **FTS5 needs real migrations in tests.** `Base.metadata.create_all()`
   does not create virtual tables or triggers. Use the `db_session`
   fixture in `tests/db/conftest.py`, which shells out to Alembic.

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

---

## Known gaps / deliberate deferrals

- **Auth is configured but not enforced.** `AuthConfig` exists; no
  middleware or login route yet. `docker-compose.yml` binds to
  `127.0.0.1` and carries a comment about this. Must land before anything
  is exposed beyond localhost.
- **`db/models.py` is Phase-1-scoped.** `change_sets`, `changes`,
  `apply_journal`, `blobs`, `jobs`, `provider_cache` come in Phase 2+.
- **`Track.tag_hash` and `content_hash` columns exist but are unused** —
  the scan pipeline should start populating them; they become the drift
  detection mechanism in Phase 2.
- **No writes anywhere yet**, by design. Phase 1 is safe to point at a
  real library.
