# muzilla — Implementation Plan

> **Reading convention: normative vs illustrative.**
>
> This plan is written at a design altitude, without executing code.
> Some of it is a load-bearing contract; some is a sketch that was never
> run. The two are not always distinguishable from tone, so:
>
> - **Normative** — architecture, layering, data-model shape, and every
>   ★-marked decision. Deviating from these needs a recorded reason.
> - **Illustrative** *(assume unverified)* — **all example templates,
>   example endpoint signatures, example field and function names, and
>   example code blocks.** These were written to convey intent, not
>   copied from working code. Where one contradicts
>   `domain/fields.py` or the actual service signatures, **the code
>   wins** — implement the intent, note the divergence in the commit
>   body, and move on. This is pre-authorized latitude, not drift.
>
> §10 already self-labels as "representative"; §6's templates and
> function list are illustrative in exactly the same way (its
> `$albumartist` examples name a field that never existed — the
> canonical field is `album_artist`).

## Context

Tagging audio files is tedious. The two existing tools each fall short: **MusicBrainz Picard** is locked to MusicBrainz APIs, and **beets** is a whole library manager where metadata is only one concern — plus it is CLI-only, with a [web plugin that admits it "can't do much right now"](https://github.com/beetbox/beets/blob/v1.3.8/docs/plugins/web.rst).

**muzilla** is a self-hosted music *metadata* manager, scoped deliberately: it never plays audio, never manages a listening library, and never owns your directory layout beyond a rename you explicitly approve. It reads tags, fetches better ones from several sources at once, shows a field-level diff, and writes only when you accept.

Core UX loop: **browse catalog → select → edit or fetch from remotes → preview diff → accept → apply (undoable)**.

Deployment target: single Docker container on an Ubuntu mini-PC, GUI over LAN. Published to GitHub as a public OSS project.

### ★ The defining constraint: a flat, mixed, unevenly-tagged library

The user's library is **one flat folder** — no subdirectories at all — containing **roughly half albums and half loose singles**, with **tag quality that varies wildly by era** of the collection. Files stay flat; renaming affects the **filename only**, never directory structure.

This is not a detail — it invalidates the single most load-bearing heuristic in beets (and in muzilla's own first draft): *"the directory is the album."* In a flat library that heuristic is not 90% right, it is **0% right**. Three consequences ripple through the whole design:

1. **Grouping must be inferred from tags + fingerprints, never from paths.** See §7, which is rewritten around this.
2. **Singletons are first-class, not a special case.** beets treats loose tracks as a grudging `-s` flag on a separate code path. Here they are ~50% of the library, so recording-level matching sits beside release-level matching as an equal peer throughout the data model, matching engine, and UI.
3. **Fingerprinting moves from Phase 6 to Phase 3.** With no directory signal and unreliable tags, AcoustID is frequently the *only* trustworthy identifier. It is no longer an optional enrichment; it is a primary identification path.

### Decisions locked with the user

| Decision | Choice |
|---|---|
| **Library shape** | **One flat folder**, ~50/50 albums and singles, tag quality varies by era |
| **Target layout** | **Stays flat** — path templates rename the *filename only*, no directory creation |
| File safety | **In-place writes with full undo** — snapshot prior tag state before every write |
| **Source model** | **One release, one source (beets model)** — pick a candidate, all tags come from it. **No per-field cross-source merging.** Source priority is a ranking tie-breaker only. Album art is the sole exception (chosen independently by quality) |
| Auth | **Single password + cookie session**, password via env var |
| Scale | **~10k–100k tracks**, FLAC + MP3 primary (full format matrix supported) |
| **Fingerprinting** | **Phase 3, on by default** *(moved earlier — see constraint above)* |
| Audio analysis | **ReplayGain via `rsgain` only** — no BPM/librosa, no key detection |
| Album art | **Embedded primarily** (a flat folder can hold only one `cover.jpg`, so folder art is near-useless here; still supported for future reorganization) |
| Repo | **Full public OSS scaffold** — MIT, CI, GHCR publishing, conventional commits |
| **Design** | **Port the whole design system in Phase 0** — all 5 token files + all 9 components from the Claude Design project (see §9), not just the Change Review screen's direct dependencies |
| **Build order** | **Phase 0 first**, then phases in sequence. Change Review is built in Phase 2 against a real backend — no throwaway static prototype |

**Scope note:** dropping librosa/BPM keeps the image near ~550 MB instead of ~1.3 GB. BPM remains listed in `domain/fields.py` as a readable/editable field — muzilla just won't *compute* it. Adding fingerprinting back adds `fpcalc` (~2 MB), which is negligible.

---

## Tech Stack

| Layer | Choice | Rationale |
|---|---|---|
| Core | Python 3.12 library, no web/CLI deps | CLI and API are both thin shells over it |
| Tags | **mutagen** | Widest format support, pure Python, zero native deps |
| DB | **SQLite** (WAL) + SQLAlchemy 2.0 + Alembic | Single-file, zero-admin, correct for one container |
| API | **FastAPI** + Pydantic v2 | Async fits provider fan-out; OpenAPI → typed TS client |
| CLI | **Typer** | Shares core + Pydantic models |
| GUI | **React 18 + TS + Vite** → static assets served by FastAPI | One container, one port |
| Server state | **TanStack Query** + **TanStack Table** (virtualized) | 90% of state is server state; Redux would re-implement it badly |
| Client state | **Zustand** (small: selections, filters) | |
| UI kit | **Tailwind + shadcn/ui** (Radix) | Accessible dialogs/menus the diff UI needs |
| Jobs | asyncio worker + SQLite job table | **No Redis/Celery** — single container is a hard constraint |
| Fuzzy match | **rapidfuzz** | C++ backed, 10–50× faster than difflib; this is a hot loop |
| Track align | **scipy** `linear_sum_assignment` | Hungarian algorithm; wheels, no build step |
| Deploy | Multi-stage Dockerfile + docker-compose | `docker compose up` |

**Note:** local Python is 3.9; dev setup needs 3.12 (Docker carries it).

---

## Guiding Architectural Decisions

1. **The core is a sync, side-effect-explicit Python library.** No FastAPI import below `muzilla/api/`; no Typer below `muzilla/cli/`. Enforced by an `import-linter` contract in CI.
2. **Nothing touches disk until a `ChangeSet` is applied.** Every mutation becomes rows in `changes` first. One concept serves four jobs: the diff UI's data source, the undo mechanism, the audit log, and crash recovery.
3. **Providers are dumb; matching is smart.** A provider returns normalized candidates — it never scores or decides. All ranking lives in one testable, network-free module.
4. **Async only at the edges.** Worker loop and HTTP client are async; tag I/O, matching, and DB writes are sync functions in a thread pool. mutagen and SQLAlchemy are sync; pretending otherwise creates bugs.

### Where beets should NOT be copied

- **Its plugin event-bus** (`plugins.send()` global dispatch) — makes control flow untraceable. Use explicit Protocol-based registries.
- **Its "library is the database" conflation.** muzilla's DB is a **cache/index over the filesystem**; *files are the source of truth*. Users will edit with Picard/foobar behind muzilla's back, so drift must be **detected**, not steamrolled.
- **Its blocking in-memory importer.** muzilla's is a resumable, persisted state machine — closing a browser tab must lose nothing.
- **Its global config singleton** (`confuse`) — makes tests order-dependent. Pass a `Config` object explicitly.
- **Its flexible-attribute EAV table** — destroys query performance and type safety. Use typed columns for the known field universe plus one `extra_tags` JSON column for the long tail.

---

## Package Structure

```
muzilla/
├── pyproject.toml              # hatchling; deps grouped [api] [cli] [audio] [dev]
├── docker/Dockerfile           # multi-stage: node build → python runtime
├── docker-compose.yml
├── migrations/versions/        # alembic
├── frontend/                   # React+TS+Vite → built into muzilla/web/static/
└── src/muzilla/
    ├── config/                 # schema.py (pydantic-settings), loader.py, defaults.yaml
    ├── db/                     # engine.py (WAL pragmas), models.py, types.py, repo/
    ├── domain/                 # PURE: no I/O, no DB, no network
    │   ├── fields.py           # ★ canonical field registry — everything derives from this
    │   ├── metadata.py         # frozen dataclasses: TrackMeta, AlbumMeta, ArtworkRef
    │   ├── ids.py              # MBID/DiscogsID/ISRC normalization
    │   └── normalize.py        # artist-credit joining, "feat." handling, unicode fold
    ├── tags/                   # reader.py, writer.py, mapping.py, strip.py
    ├── providers/              # base.py (Protocols), ratelimit.py, cache.py,
    │                           # musicbrainz/discogs/deezer/acoustid/coverartarchive/lrclib
    ├── matching/               # distance.py, weights.py, string_dist.py,
    │                           # track_align.py, candidates.py, engine.py
    ├── audio/                  # replaygain.py (rsgain), fingerprint.py, probe.py
    ├── changes/                # ★ THE CORE FEATURE
    │                           # builder.py, differ.py, applier.py, undo.py, conflicts.py
    ├── paths/                  # lexer.py, parser.py, compile.py, functions.py, sanitize.py
    ├── pipeline/               # scan.py, grouping.py, importer.py, enrich.py
    ├── jobs/                   # queue.py, worker.py, registry.py, progress.py
    ├── services/               # ← the ONLY layer CLI and API may call
    ├── api/                    # app.py, deps.py, schemas/, routers/
    ├── cli/                    # main.py, commands/
    └── web/static/             # vite build output (gitignored)
```

**Layering contract** (enforced by `import-linter`):
```
domain          → (nothing)
tags, paths     → domain
providers       → domain
matching        → domain, providers
db              → domain
changes         → domain, db, tags, paths
pipeline, jobs  → all above
services        → all above
api, cli        → services ONLY
```

`api` and `cli` may not import `db.models`. That one rule makes CLI/API divergence architecturally impossible — `muzilla match album <id> --apply` is:

```python
proposal = match_service.propose(session, album_id, sources=cfg.providers.enabled)
cs = changeset_service.create_from_proposal(session, proposal)
render_diff(cs)                       # the ONLY CLI-specific code
if apply or typer.confirm("Apply?"):
    changeset_service.apply(session, cs.id)
```

`POST /api/albums/{id}/match` calls the same three service functions.

---

## Key Subsystems

### 1. Canonical field registry (`domain/fields.py`)

**Write this first.** Defines the field universe once: name, type, multi-valued?, per-format tag mappings, participates-in-matching?, default strip policy. Tag mapping, diff labels, path template variables, API schemas, and UI forms all derive from it. Adding a field = editing one file. Highest-leverage decision in the data model.

### 2. Provider abstraction (`providers/`)

Four narrow Protocols rather than one fat one (Cover Art Archive shouldn't stub `search_releases`):

```python
class MetadataProvider(Protocol):
    name: str; capabilities: frozenset[Capability]; requires_auth: bool
    async def search_releases(self, q: ReleaseQuery, limit: int) -> list[ReleaseCandidate]: ...
    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None: ...
    async def health(self) -> ProviderHealth: ...

class ArtProvider(Protocol): ...
class LyricsProvider(Protocol): ...
class FingerprintProvider(Protocol): ...
```

`ReleaseQuery` is normalized input; providers use what they can and ignore the rest (Deezer has no barcode search). **Never let query shape be dictated by the richest provider.**

`ReleaseCandidate` is normalized output with `external_ids` and `art_refs`. It always carries the `source` it came from — a candidate is one release from one provider, and staging it applies its fields wholesale (§3).

**Rate limiting** — async token bucket per provider on a shared context:

| Provider | Limit | Concurrency | Auth |
|---|---|---|---|
| MusicBrainz | 1 req/s | **1 + asyncio.Lock** | none |
| Discogs | 60/min | 4 | **PAT required** |
| Deezer | 50/5s | 8 | none |
| AcoustID | 3/s | 2 | **free API key** |
| Cover Art Archive | 5/s | 4 | none |
| LRCLIB | 10/s | 8 | none |

- MB gets a hard lock, not just a bucket — its server-side limiter 503s on overlapping requests even at nominal 1/s.
- Buckets are **process-global**; interactive UI searches get priority over import jobs via `acquire(priority=...)`.
- Retry with jittered backoff on 429/503, honoring `Retry-After`. **A dead provider never fails the match** — proceed with whoever answered.
- Compliant `User-Agent: muzilla/0.x ( <repo-url> )` — MB blocks otherwise.
- **Zero-config first run is already useful**: MB + Deezer + LRCLIB + CAA need no tokens. Real advantage over Picard.

**Two cache layers, deliberately:**
- **HTTP cache** (`hishel` over httpx) — respects `ETag`/`Cache-Control`, handles restarts/retries.
- **Semantic cache** (`provider_cache` table) — stores *normalized* candidates, keyed `(provider, operation, query_hash)`. Raw HTTP becomes useless when normalization code changes; normalized results let matching re-run offline (critical for tests and weight tuning). TTLs: releases 30d, searches 7d, lyrics 90d, fingerprints 180d.

> **Risk:** Discogs' terms prohibit bulk redistribution. Cache for personal use is fine — do not build any "export the cache" feature.

### 3. Matching engine (`matching/`)

Pure functions, tunable against a fixture corpus.

**Weighted distance accumulator** (beets-style, worth borrowing) — normalized `Σ(w·d)/Σw` in [0,1]:

```python
ALBUM_WEIGHTS = {"album": 3.0, "album_artist": 3.0, "tracks": 2.0,
                 "missing_tracks": 0.9, "unmatched_tracks": 0.6, "year": 0.5,
                 "media": 0.5, "country": 0.5, "label": 0.5, "catalog_number": 0.5,
                 "album_id": 5.0, "barcode": 2.0, "source": 2.0}
TRACK_WEIGHTS = {"title": 3.0, "artist": 2.0, "length": 2.0,
                 "index": 1.0, "track_id": 5.0, "isrc": 4.0}
```
User-overridable in config — cheap to expose, valuable for classical/vinyl collections.

**String distance** (`string_dist.py`) — not naive Levenshtein:
1. NFKD → strip combining marks → casefold
2. Strip leading articles (configurable per language)
3. Normalize punctuation; strip bracketed content **only against a curated noise regex list** (`(Remastered 2011)`, `[Explicit]`) — never blindly, since `(Live at Budokan)` is meaningful
4. Canonicalize `feat.|ft.|featuring|with`
5. Roman numeral ↔ digit unification (`Pt. II` ↔ `Part 2`) — matters hugely for classical/prog
6. `min(1 - token_set_ratio/100, 1 - ratio/100)` via rapidfuzz

**Track alignment — where beets is weakest.** beets uses greedy sequential comparison, which breaks on out-of-order rips, hidden tracks, and bonus discs. Instead: build cost matrix `C[i][j]`, solve with `scipy.optimize.linear_sum_assignment` (Hungarian, trivially fast at album scale). Pad with dummy columns at `MISSING_COST = 0.85`. Two refinements:
- **Sequential prior**: add `0.15 × |i−j| / max(n,m)` — breaks ties toward natural order without forbidding reordering
- **Disc-aware**: solve per-disc when disc numbers or `CD1/`-style subdirs are reliable

**★ Multi-source candidate gathering — one release, one source (beets model)**

muzilla queries several sources in parallel, but **a candidate is always one release from one source, and every tag applied comes from that single chosen release.** This is beets' model and it is the correct one.

An earlier draft of this plan specified per-field cross-source merging (title from MusicBrainz, label from Discogs, genre from Deezer, with a `field_sources` priority list and per-field source switching in the UI). **That design is rejected.** It produces metadata describing no actual release: MusicBrainz's tracklist for a 2011 remaster silently paired with Discogs' catalog number for the 1999 original pressing is internally inconsistent in a way the user cannot see. Release-level coherence is worth more than best-of-breed fields.

`candidates.py`:
1. `asyncio.gather` over enabled providers, exceptions caught per-provider — a dead provider never fails the match
2. **Score each candidate independently** against the local group
3. **Rank into one flat list**, ordered by distance. Each row is `(source, release)` — MusicBrainz's and Discogs' record of the same release are **two separate, individually pickable rows**
4. **Duplicate flagging, not merging.** When two candidates look like the same release (shared barcode, shared MBID — Discogs often carries MB links — or artist+album distance < 0.1 with equal track count and |year| ≤ 1), mark them as alternatives of one another so the user can see at a glance that they are the same record seen twice, rather than two different pressings. This is a **visual hint only**; it never merges data and never changes what picking a row applies.
5. **Corroboration adjusts ranking, not content.** A candidate corroborated by an independent source gets a modest distance bonus (`−0.05 × (n_corroborating − 1)`), because agreement across sources is real evidence the match is right. The tags still come wholly from the picked row.
6. Rank thresholds: `< 0.10` auto-applicable in `--quiet`; `0.10–0.25` needs confirmation; `> 0.25` always human.

**Source preference is a tie-breaker, not a per-field merge.** Config expresses which source to *prefer when candidates score equally*, applied to the whole release:

```yaml
matching:
  source_priority: [musicbrainz, discogs, deezer]
  source_penalty: 0.02   # distance added per step down the priority list
```

**Album art is the one deliberate exception** (per the user's decision). Art is not really a tag — Deezer and MusicBrainz carry no artwork of their own, so it comes from Cover Art Archive or a provider CDN regardless of which release was picked. Art is therefore chosen independently by resolution/quality, and the art diff row shows its own source. Everything else obeys the single-source rule.

**AcoustID short-circuit (Phase 3, on by default):** before text search, if fingerprints exist, query AcoustID; any MBID appearing in ≥50% of a group's tracks triggers a direct MB release lookup entering the pool with a strong prior. This is the *only* viable path for `track01.mp3`-style untagged files — and with era-varying tag quality in a flat folder, a meaningful share of the library is exactly that.

**Singleton matching is a separate, equal path.** Roughly half this library is loose tracks, so `matching/engine.py` exposes two entry points, not one:
- `propose_for_group(group)` — release-level, using `ALBUM_WEIGHTS` and Hungarian track alignment (above)
- `propose_for_singleton(track)` — **recording-level**, which is a genuinely different problem: no tracklist to align, no track-count or media corroboration, so the signal is title + artist + duration + ISRC + fingerprint. Weights differ accordingly (`{"title": 3.0, "artist": 3.0, "length": 2.5, "isrc": 4.0, "acoustid": 5.0}`), and thresholds are **stricter** — with fewer corroborating signals, a confident-looking wrong match is easier to produce, so auto-apply requires distance < 0.06 rather than 0.10.

A singleton match must also decide *which release to credit*, since one recording appears on the original album, three compilations, and a deluxe reissue. Default policy: **prefer the earliest official release** (`original_year`), configurable to prefer album-over-compilation, with the alternatives listed as separate pickable candidates. Getting this wrong is what fills a library with "Now That's What I Call Music 47" as the album for every single. Same rule as albums: picking a candidate takes that release's tags wholesale.

### 4. Staged changes & diff — the heart of the system

**ChangeSet** — a proposed, reviewable, atomically-applicable unit with source (`manual_edit`, `match_proposal`, `rename`, `strip_tags`, `undo_of:<id>`), scope, state, and N Changes.

**Change** — one field on one entity: `(entity_type, entity_id, field, old_value, new_value, op, provenance, decision)`. Field-level granularity is **non-negotiable** — users must accept the title fix while rejecting the genre change.

```
DRAFT ──edit/decide──▶ DRAFT
  ├─ discard ─────────▶ DISCARDED
  └─ apply ──▶ APPLYING ──┬─ ok ──▶ APPLIED ──undo──▶ REVERTED
                          ├─ partial ▶ PARTIALLY_APPLIED
                          └─ fail ──▶ FAILED (compensated)
```

Each Change independently carries `decision ∈ {pending, accepted, rejected}`; apply processes only `accepted`. Defaults configurable per change *kind* (auto-accept `strip` ops for always-strip tags; leave genre pending).

**Diff computation** (`differ.py`) produces a `FieldDiff` per change:
- **Char-level inline diff** for strings via `difflib.SequenceMatcher` opcodes → spans. Seeing that `"Beatles" → "The Beatles"` changed only at the head is the difference between a usable and an infuriating review UI.
- **Binary fields** (art) never diff by content: `kind="binary"`, display `{w}×{h} {mime} {size}` + thumbnail URLs. Never put image bytes in the diff payload.
- **Multi-valued fields** (artists, genres) diff as ordered sets → added/removed/reordered, not joined strings.
- **Moves** render as before/after paths with the differing segment highlighted.
- **Severity**: clearing a populated field or moving a file is `destructive`; UI requires an explicit toggle to bulk-accept those.

**Apply — atomicity, stated honestly.** True cross-file ACID is *impossible*; files are separate FS objects. The design gives crash-**recoverable**, not crash-**atomic**, semantics via write-ahead journal + per-file atomic replace:

1. **Probe** — re-read file, recompute `tag_hash`. Differs from stage time → **conflict**, abort that file, mark `conflicted`. *(This is the beets divergence: files are truth, so drift is detected, not steamrolled.)*
2. Journal row `PENDING` with `before_blob` = complete original tag payload (<5 KB; art in blob store)
3. Copy to `<target>.muzilla.tmp` **in the same directory** (same FS → atomic rename), write tags, `fsync`
4. `os.replace(tmp, target)` — atomic on POSIX
5. Optionally preserve `mtime` (other tools key off it)
6. Journal → `DONE` with `after_hash`

Renames run **after** all tag writes succeed (a rename invalidates paths), also via `os.replace`, plus guarded empty-parent pruning (never above library root, never non-empty, never follow symlinks).

On failure: `abort_all` (default) reverts written files from journal `before_blob` — a **compensating action, not a rollback**, and the docs should say so.

**Startup recovery:** scan for `PENDING`/`WRITING` journal rows, compare on-disk hash against `before_hash`/`after_hash`, restore from `before_blob` if indeterminate, mark ChangeSet `FAILED` with a recovery note surfaced in the UI.

**Undo is not a special mechanism** — it's a synthesized inverse ChangeSet (`source="undo_of:<id>"`) run through the *identical* apply path. Falls out for free: undo is previewable as a diff, partially acceptable, undo-of-undo is redo, and undo conflicts are caught by the same hash check.

Retention: journals with `before_blob` kept 30 days / 500 changesets (whichever first), then pruned and marked `undo_expired`. Art blobs are content-addressed and refcounted so a 20 MB cover isn't stored 12× for a 12-track album.

### 5. Data model (`db/models.py`)

**`tracks`** — `path` (UNIQUE, absolute, NFC), `dir_path` (indexed), `size_bytes`, `mtime_ns`, `content_hash`, `tag_hash`, format/duration/bitrate, then canonical metadata columns (title, artist, `artists` JSON, album, album_artist, track_no/total, disc_no/total, year, original_year, `genre` JSON, label, catalog_number, isrc, barcode, comment, bpm, key, compilation), provider IDs (`mb_*`, `discogs_release_id`, `deezer_track_id`, `acoustid_*`), ReplayGain (`rg_track_gain/peak`, `rg_album_gain/peak`, `r128_track_gain`), `has_lyrics`, `lyrics_synced`, `has_embedded_art`, `art_blob_id`, `extra_tags` JSON, `album_id` FK, timestamps, `missing_since`.

> `content_hash` is **not** a full-file hash — ruinous on 500 GB. Use `blake2b(first_64KB + last_64KB + size)`. For drift detection the real check is `tag_hash` (cheap — tags are already read). Rescan fast path: `(size, mtime_ns)` unchanged → skip.

Indexes: `path` unique, `dir_path`, `album_id`, `mb_release_id`, partial on `acoustid_fingerprint`, **plus FTS5 virtual table** `tracks_fts(title, artist, album, album_artist)` with triggers. FTS5 ships with SQLite; `LIKE '%foo%'` over 100k rows is unusable.

**`track_groups`** *(replaces `albums`; renamed because ~half of them aren't albums)* — a *derived, correctable* grouping: `id`, `key` (stable hash of the identifying fields), `kind` (**`album | singleton | partial_album | unknown`**), `grouping_basis` (`release_id|barcode|fingerprint|tags|singleton|manual`), `grouping_confidence` (float), `is_pinned` (bool — user corrected it, never re-guess), album metadata (`album`, `album_artist`, `year`, `track_count`, `disc_count`, `label`, `catalog_number`, `barcode`), `expected_track_count` (from the matched release, to drive "3 of 12" indicators), `mb_release_id`, `discogs_release_id`, `art_blob_id`, `is_compilation`, `match_state` (`unmatched|proposed|matched|manual`), `match_distance`.

> **No `dir_path` column.** It was a grouping input in the first draft and is deliberately removed — in a flat library it is a constant and would silently collapse the entire catalog into one group.

**`change_sets`** — UUID7 id, `title`, `source`, `source_ref` JSON, `state`, `scope_type/id`, `created_by` (`cli|web|job`), `job_id`, `stats` JSON, `undo_of_id` self-FK, `expires_at`, `error`. Plus **`candidate_source`** and **`candidate_ref`**: the single chosen `(provider, release id)` the whole changeset was staged from — nullable, since manual-edit changesets have no candidate. Re-picking a candidate rewrites the changeset's `changes` rows and updates these two columns.

**`changes`** — `change_set_id` FK CASCADE, `seq`, `entity_type/id`, `field`, `op` (`set|clear|strip|append|move|embed_art|write_lyrics`), `old_value`/`new_value` JSON, `old_blob_id`/`new_blob_id`, `confidence`, `severity`, `decision`, `apply_state`, and **`is_manual`** (bool — the user typed this value, so it no longer matches the chosen release).

> **No per-field `provenance` column.** An earlier draft had one, to record which source won each field. With one release per changeset the source is a property of the *changeset*, not of each row — a per-field column would only ever hold one repeated value, and its existence would invite exactly the per-field merging the design rejects. `is_manual` captures the one real per-row distinction.

**`apply_journal`** — `change_set_id`, `track_id`, `path`, `phase` (`tags|move|art`), `state`, `before_hash`, `after_hash`, **`before_blob` JSON**, `before_path`, `after_path`, `error`. Both crash-recovery log and undo source.

**`blobs`** — content-addressed: `sha256` UNIQUE, `mime`, `size`, `width`, `height`, `storage_path` (sharded **on disk, not in SQLite** — blobs bloat the DB and wreck WAL checkpointing), `refcount`.

**`jobs`** — `type`, `state`, `priority`, `payload`/`result` JSON, `progress_current/total/message`, `attempts`, `lease_until`, `worker_id`, `parent_job_id`, `cancel_requested`. Index `(state, priority, created_at)`.
**`job_events`** — append-only, for SSE replay after reconnect.
**`provider_cache`**, **`import_sessions`** + **`import_tasks`** (resumable state machine), **`settings`**, **`users`** (single row for the password hash).

**SQLite pragmas** (`db/engine.py`):
```sql
PRAGMA journal_mode=WAL;      PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=10000;    PRAGMA foreign_keys=ON;   -- per-connection! default is OFF
PRAGMA cache_size=-64000;     PRAGMA mmap_size=268435456;
```
**Single-writer discipline from day one**: all writes route through one worker-owned session; API handlers read freely (WAL allows concurrent readers) and *enqueue* writes. Very hard to retrofit.

### 6. Path/filename template engine (`paths/`) *(examples illustrative)*

**Full beets-equivalent path templating — the user defines the directory structure.** The engine renders complete relative paths; whether those paths contain `/` (and therefore create directories) is entirely a function of the template the user writes. muzilla imposes no structure of its own.

**Ships flat, foldering ready to enable.** Default config is filename-only to match the current library, with directory support built and tested in the same phase:

```yaml
paths:
  create_directories: false          # ← default; flip to true for beets-style foldering
  album:     "$albumartist - $album - $track $title"
  singleton: "$artist - $title"
  default:   "$artist - $title"
```

Flipping the toggle and writing a template with separators gives the familiar beets layout:

```yaml
paths:
  create_directories: true
  album:      "$albumartist/$album%aunique{}/$track $title"
  singleton:  "Singles/$artist - $title"
  comp:       "Compilations/$album%aunique{}/$track $title"
  "genre:Classical": "Classical/$composer/$album/$track $title"
```

All of it is user-defined — alphabetical buckets (`%upper{%left{$albumartist,1}}/$albumartist/...`), year-based, genre-based, or per-query overrides like beets' `paths:` map keyed by query. **No structure is hardcoded.**

Note the **separate `singleton:` template** — with ~50% loose tracks, rendering `Artist - Album - 00 Title` for a single with no album is exactly the wrong output.

beets-compatible syntax: `$field`, `${field}`, `%func{a,b}`, nesting, `$$` escapes. Functions `%upper %lower %title %left %right %if %ifdef %asciify %time %first %aunique %sunique %the`, plus muzilla additions `%pad{$track,2}`, `%sanitize{}`, `%default{$field,text}`.

**Two rendering modes, one engine:**
- `create_directories: false` — a rendered `/` is a **validation error** surfaced in the template editor (not a silently-created folder). Rename preview is a flat before/after list.
- `create_directories: true` — separators create directories; `mkdir -p` semantics with the same guardrails as the move path (never above library root, never follow symlinks), plus **empty-source-directory pruning** after moves. Rename preview is a **file-tree** showing directories to be created.

> **Collisions matter most in flat mode.** Every file shares one namespace, so two different releases of the same song collide under `$artist - $title`. Flat mode therefore checks collisions against the *entire library* and makes `%aunique` effectively mandatory; foldered mode checks per-directory, as beets does. Either way collisions surface in the diff at stage time, and the rename job refuses to run while any remain unresolved.

**Recommendation: hand-written recursive-descent lexer + parser → AST → compiled closure tree.** Not regex (nesting like `%if{$comp,%upper{$aa},$artist}` is not a regular language — beets' regex-ish approach has a long tail of escaping bugs, don't inherit them), not a parser generator (~10 productions doesn't justify the dependency). ~250 lines, zero deps, precise error offsets (the UI must show *where* a template is malformed), compiles once so rendering 50k paths doesn't reparse.

Functions receive **unevaluated thunks**, so `%if` short-circuits properly — beets evaluates eagerly and papers over it.

**`%aunique` needs DB context**, so the render context carries a `DisambiguationResolver` returning the first disambiguator that separates colliding albums (year → label → catalog# → MBID prefix), **memoized per album per batch** (naive impl issues a query per track).

> **Ordering trap:** `%aunique` must resolve against the **projected post-change album set**, not current DB state — otherwise you create collisions that only appear after apply.

**Sanitization** — replace `<>:"/\|?*` and control chars; strip trailing dots/spaces (**critical for SMB/NAS on a mini-PC**); reject Windows reserved names (`CON`, `NUL`, `LPT1`); clamp each *component* to 255 bytes on grapheme boundaries; NFC-normalize (macOS SMB clients otherwise create duplicate-looking dirs); configurable `replace:` regex list.

**Collisions** surface at *stage* time in the diff, before any move. `muzilla path-test '<tmpl>' --album <id>` + a live-preview endpoint let users iterate without touching files.

### 7. Scan/import pipeline (`pipeline/`)

1. **Walk** — `os.scandir`, symlinks off by default (loop risk), ignore globs, extension filter, 500-path batches. Fast path `(size, mtime_ns)` unchanged → skip. A 100k rescan should take seconds.
2. **Probe & read** — mutagen `File(path, easy=False)` (need raw frames), map via `tags/mapping.py`, upsert. Parallel `ThreadPoolExecutor` (mutagen releases the GIL on read). Corrupt files → log, mark `probe_error`, **continue** — never abort a 40k scan on one bad MP3.
3. **Fingerprint** *(Phase 3, on by default)* — bounded concurrency `min(4, cpu_count)`; `fpcalc` is CPU-heavy and will starve the API on a mini-PC. **Batch AcoustID lookups** (the API accepts multiple fingerprints per request). Runs *before* grouping here, because in a flat library the fingerprint is often the only reliable grouping signal.
4. **Group** — see §7b below. **No directory signal exists**; grouping is inferred from tags and fingerprints and is always a *correctable proposal*.
5. **Match & stage** — build query, gather, score, merge, produce `ChangeSet` in `DRAFT`. Albums match against releases; singletons match against **recordings**. Auto-apply only if distance < threshold **and** `--quiet` **and** no `destructive` changes; else it lands in the review inbox.

### 7b. Grouping in a flat library (`pipeline/grouping.py`) — ★ rewritten for this constraint

Directory-based grouping is **deleted from the design**, not merely deprioritized. `albums.dir_path` is dropped; `tracks.dir_path` remains only for future-proofing and is never a grouping input. The replacement is a **confidence-scored cascade**, where every group carries a `grouping_confidence` and a `grouping_basis` explaining *why* muzilla thinks these files belong together — because in a flat folder the tool is guessing, and it must say so.

**Stage 1 — Strong identifiers.** Group by `mb_release_id` where present, then by `barcode`, then `(catalog_number, label)`. These are unambiguous when they exist. `basis="release_id"`, confidence 1.0.

**Stage 2 — Fingerprint consensus.** For files with AcoustID results, collect returned MBIDs. Any release MBID shared by ≥3 files (or ≥50% of a candidate group) forms a group with `basis="fingerprint"`, confidence 0.9. **This is the workhorse for the badly-tagged era of the collection** and the main reason fingerprinting moved to Phase 3.

**Stage 3 — Tag clustering with fuzzy keys.** Normalize `(album_artist, album, year)` through the same `string_dist.py` pipeline used for matching (article stripping, unicode fold, punctuation, `feat.` canonicalization), then cluster with a distance threshold rather than exact equality — so `"Beatles"`/`"The Beatles"` and `"Abbey Road"`/`"Abbey Road (Remastered)"` land together. `basis="tags"`, confidence scaled by intra-cluster tightness (0.5–0.85).

**Stage 4 — Singleton classification.** A file becomes a **singleton** when any of: no album tag; album tag equals title (very common for singles); `track_total == 1`; or it survives Stages 1–3 as a cluster of one. `basis="singleton"`.

**Partial-album detection.** When a cluster matches a release of N tracks but holds only M < N, muzilla **cannot distinguish "incomplete rip" from "I only ever wanted these 3 songs"** — a genuine ambiguity that directory-based tools sidestep. So it never guesses: the group is flagged `partial`, shown with a "3 of 12 tracks present" indicator, and the user decides between *treat as partial album* (keep album tags, track numbers stay 3/12) and *treat as singles* (strip album context). This choice is remembered per group.

**Everything is correctable.** Grouping proposals are persisted in `track_groups` and editable in the UI: merge two groups, split one, drag a track between groups, or force a track to singleton. **A grouping correction is itself a ChangeSet**, so it is previewable and undoable like everything else. Corrections are sticky — a `group_pin` survives rescans so muzilla never re-guesses something you already fixed.

> **Why this matters more than it looks:** with ~50% singles and era-varying tags, a wrong grouping produces a wrong *release* match, which produces wrong tags on a dozen files at once. Grouping errors are the highest-leverage error class in this library shape, which is why the design makes them visible and cheap to fix rather than trying to be silently clever.

Enrichment (ReplayGain, lyrics, art) is deliberately **separate jobs** — slow, optional, independently re-runnable. Coupling them to import makes import unusably slow.

### 8. Configuration (`config/`)

Precedence (low → high): packaged defaults → `/etc/muzilla/config.yaml` → `$MUZILLA_CONFIG_DIR/config.yaml` → `--config` → `settings` DB table → `MUZILLA_*` env → CLI flags.

Env sits **above** the DB deliberately: in Docker, env is how the operator asserts control; a UI toggle must not override `MUZILLA_PROVIDERS__DISCOGS__ENABLED=false`. The API returns `{value, source, overridden_by}` per setting so the UI can grey out env-locked fields.

`pydantic-settings` with `env_nested_delimiter="__"` → `MUZILLA_PROVIDERS__DISCOGS__TOKEN` maps to `config.providers.discogs.token` free, typed and validated.

**Secrets:** `SecretStr` throughout (auto-redacted `__repr__` prevents the classic token-in-a-traceback leak); `_FILE` suffix convention (`..._TOKEN_FILE=/run/secrets/discogs`) for Docker secrets; never written to the `settings` table (UI writes to `secrets.yaml`, mode 0600, gitignored, excluded from config export) and only ever shown masked. `muzilla config check` validates each token with one authenticated call. **Missing token degrades gracefully** — provider disabled with a UI banner, never a crash.

**Auth (per user's choice):** password hashed with `argon2` from `MUZILLA_AUTH__PASSWORD`, signed HttpOnly cookie session, `MUZILLA_AUTH__ENABLED=false` escape hatch. Compose binds `127.0.0.1` by default with reverse-proxy guidance in the README.

### 9. Frontend (`frontend/`)

#### ★ Design source of truth

A Claude Design project — **"Muzilla design system foundation"** (`2e44cd36-f250-4a65-95bd-24ee760775a3`) — already exists and is readable via the `DesignSync` MCP. It is not a rough mockup: it contains a complete token system, nine specced React components, and a working `Change Review.dc.html` prototype with real diff logic. **Port it, don't reinvent it.**

*(Note: it is a regular design project, so `list_projects` — which filters to design-system projects — returns empty. Read it by passing the `projectId` directly to `get_project` / `list_files` / `get_file`.)*

**Design tokens → ported verbatim as CSS custom properties** into `frontend/src/styles/tokens/{colors,typography,spacing,effects,base}.css`. Tailwind's theme maps *onto* these variables rather than replacing them, keeping the design system the single source of truth:

- `colors.css` — 10-step neutral ramp, restrained blue accent, **full dark + light themes** via `[data-theme="light"]`, and semantic aliases (`--bg-canvas`, `--text-primary`, `--border-subtle`) that components reference instead of raw ramp values
- **Diff semantics**: `--diff-added` / `--diff-removed` / `--diff-conflict` / `--diff-unchanged`, each with a paired `-bg` row tint. The file explicitly warns *"never rely on hue alone; always pair with the +/−/▲ iconography"* — that accessibility contract must survive the port
- **Provenance hues**: `--provenance-musicbrainz` (violet), `--provenance-discogs` (teal), `--provenance-deezer` (pink), deliberately lower-chroma than the diff colors so they stay subordinate
- `typography.css` — **IBM Plex Sans + IBM Plex Mono**, 10-step scale from 11px, tuned for density. **Self-host these rather than keeping the design's Google Fonts `@import`**: the container must work on a LAN with no internet, and it removes a third-party request per page load.
- `spacing.css` — 4px base scale; `effects.css` — radii, shadows, `--row-height-compact: 28px`

**Component library — nine components already specced** in `components/{buttons,data,feedback,forms}/`, each with a `.jsx` implementation, a `.d.ts` interface, and a `.prompt.md`. Already authored as React with TypeScript prop types, so they port directly:

| Component | Contract |
|---|---|
| `Button` | primary / secondary / ghost / destructive |
| `Badge` | 9 tones incl. all 3 provenance sources; `dot` prop for non-hue redundancy |
| `ConfidenceBar` | 0–100, reuses the diff color scale |
| `TableRow`, `ThumbnailTile` | catalog primitives |
| `ThreeStateToggle` | **`accept \| pending \| reject`** — the core diff-row control |
| `Input`, `Select`, `Checkbox` | form primitives |
| `Modal`, `Toast`, `ProgressBar`, `EmptyState` | feedback |

Port to `frontend/src/components/ui/` as `.tsx`, converting inline-style objects to Tailwind classes over the same CSS variables and promoting each `.d.ts` into the component file. `Badge`'s `TONES` map and `ConfidenceBar`'s threshold logic transfer as-is.

**`Change Review.dc.html` → `ChangeSetReview.tsx`.** The prototype runs on a Design Canvas runtime (`x-dc`, `sc-if`, `sc-for`, `DCLogic`, `support.js`) that React **replaces entirely** — but its *logic* is directly reusable and already validates the design:

- `diffText(old, new)` — common-prefix/suffix scanner emitting styled segments. **Exactly the char-level inline diff the plan specifies** (`"Sigur Ros" → "Sigur Rós"` highlights only the changed middle). Port to `frontend/src/lib/diff.ts`; once the backend computes `FieldDiff.inline` spans via `SequenceMatcher`, the client version stays as the fallback for locally-edited values.
- `trackChip(track)` — derives per-track state with **conflict correctly taking precedence** (`Conflict` > `Rejected` > `Accepted` > `Mixed`)
- Keyboard handling — `j/k` navigate, `a/r` accept/reject, **`e` edit**, with an `INPUT`/`TEXTAREA` guard
- `acceptAll`, `acceptAllNonDestructive`, `acceptFieldAcrossTracks` port directly. **`winnerOverrides` does NOT** — it implements per-field source switching, which is rejected (see §3). Replace it with a single `selectedCandidateRef` for the whole changeset.
- Fixture data is a realistic 13-track *Ágætis byrjun* with diacritics, conflicts, destructive comment-clears, and a case where **Deezer wins on recency despite lower confidence and disagreement from both other sources** — an excellent adversarial fixture to keep for Storybook and tests

**Gaps between prototype and plan**, to close during the port rather than treat as design changes:

> **★ The mock's right-hand pane is wrong and must not be ported as-is.** It shows a *per-field* provenance panel: for the focused field, each of the three sources' values with a "use this" button, plus per-field `WINNER` badges and a source badge on every diff row. That is the rejected cross-source merge model (§3). **Replace it with a release-level candidate picker** — a ranked list of `(source, release)` rows where picking one re-stages the whole changeset. The mock's fixture data makes the problem concrete: track 2 takes its genre from Deezer while track 4's title comes from Deezer *against* MusicBrainz and Discogs both disagreeing — a per-field Frankenstein that corresponds to no real release. Its visual language (source badges, confidence bars, agreement indicator) is still good and transfers to the candidate rows.

- Prototype is **album-only**; the plan needs **singleton** and **bulk-singleton** modes (left pane collapses for singletons)
- Per-field source switching (`winnerOverrides`, "use this" buttons, per-row source badges) is **removed**, replaced by one chosen candidate for the whole changeset
- `applyChanges()` is an empty stub — needs confirmation modal, job creation, SSE progress
- Resolution state is `'accept' | 'reject' | 'edit'`, but `ThreeStateToggle.d.ts` says `'accept' | 'pending' | 'reject'`. **Reconcile to four states**: `pending | accepted | rejected | edited`, matching the `changes.decision` column plus an edited flag
- Prototype holds state in memory; the real screen must `PATCH` every decision so closing the tab loses nothing
- Header art is a placeholder swatch — needs the real `/api/blobs/{id}?size=thumb` endpoint

```
/                    Dashboard — counts, album/single split, recent changesets, jobs,
                     provider health, library health
/catalog             Virtualized TRACK table (the primary view — see below); facets
                     (artist/album/genre/year/format/missing-art/unmatched/
                     grouping-confidence/singles-only); multi-select
/groups              Grouping workspace — review/merge/split/pin inferred groups
/groups/:id          Group detail (album or singleton) + "Match" action
/import              Import wizard; /import/:sessionId  Review inbox
/changes             ChangeSet list (draft/applied/reverted) + undo
/changes/:id         ★ DIFF REVIEW — the most important screen
/jobs                Job list + live log stream
/settings            Providers/tokens, filename templates + live preview, strip rules, weights
```

> **The catalog is track-first, not album-first.** With ~50% singles and no directory structure, an album-centric browse view would misrepresent half the library. Tracks are the primary unit; album grouping is an *overlay* (group-by toggle) rather than the organizing principle. This is a deliberate inversion of how beets and Picard present a library, and it follows directly from the flat/mixed constraint.

**Two review modes, not one.** The diff review screen must handle both shapes:
- **Album mode** — the three-pane layout below, with the left pane listing the group's tracks
- **Singleton mode** — the left pane is meaningless for one track, so it collapses; the layout becomes two panes (diff + candidates) with more room for alternative releases. Since singleton matching has fewer corroborating signals, this mode leans harder on showing *which release the track was credited to* and offering alternatives.
- **Bulk-singleton mode** — reviewing 40 unrelated singles at once, where the left pane lists *tracks* rather than an album's tracklist and "accept this field across all" is dangerous rather than helpful (these files share nothing). The bulk bar adapts accordingly.

**The diff review screen is the product.** Three panes (album mode):
- **Left**: entity list with per-track change counts and accept/reject chips
- **Center**: field-level diff rows — label, old (struck/red), new (green), char-level inline highlight, confidence bar, three-state control (accept / reject / edit-manually)
- **Right**: **candidate picker** — the ranked list of matched releases, one row per `(source, release)`. Each row shows source badge, release title/artist, year, label, catalog number, track count, distance score, and a duplicate-alternative marker when another row describes the same release. Picking a row **re-stages the entire changeset from that release** via `PATCH`, replacing all proposed values at once.

> **★ Candidate selection is release-level, never field-level.** There is no per-field source dropdown, and no `field_sources` config. The right pane switches *which release you are applying*, not where an individual tag came from. A single source badge in the header states the chosen release for the whole changeset; individual diff rows carry no source badge, because they all share one. See §3 for why per-field merging was rejected.
>
> The one exception is the **album art row**, which carries its own source label since art is chosen independently by quality (Deezer/MusicBrainz have no art of their own).

**★ In-review editing.** Beyond accept/reject, the only mutation available on a proposed tag is **editing its value**:

- **Override any proposed value** — the third state of every diff row. Sets `new_value` and marks that row `manual`, so the apply path and the UI both know it no longer matches the chosen release.
- **Edit a field muzilla proposed clearing** — recover from a destructive proposal by typing the value you want instead of merely rejecting.

Manual editing of *unproposed* fields, adding fields no provider touched, and bulk field-setting live in the **manual editor** (below), not here. Keeping the review screen focused on "is this fetched release correct?" is what makes the single-source model legible — a screen that both applies a coherent release *and* lets you freely inject unrelated values undermines the guarantee it exists to provide.

Both actions produce ordinary `Change` rows in the same DRAFT changeset, so they inherit the diff, the apply path, and undo with no special cases. **There is exactly one path to disk.**

Essential details:
- **Bulk bar**: "Accept all", "Accept all non-destructive", "Reject all genre changes", and **"Accept this field across all tracks"** — nobody will click 200 times for the same album-artist fix. (No "set this field across all tracks" here; bulk *setting* belongs to the manual editor, where it isn't competing with a chosen release.) In **bulk-singleton mode** the cross-track actions are de-emphasized with a warning, since 40 unrelated singles share nothing.
- Album-level rows rendered **once**, not repeated per track
- **Rename preview**: before/after file tree, highlighted segments, collision warnings
- **Art diff**: side-by-side thumbnails + dimensions, with "keep existing" (local art is often better than CAA's)
- **Every accept/reject persists** via `PATCH` with optimistic update — closing the tab loses nothing. Direct win over beets' in-memory importer.
- **Keyboard-first**: `j/k` navigate, `a/r` accept/reject, `A` accept-all-in-entity, `Enter` apply. Users will process hundreds of albums; mouse-only is a non-starter.

**Manual tag editing — a distinct screen, not just an inline affordance.** Fetching from providers is one way to change tags; typing them yourself is the other, and it must be equally first-class:

- **Single-track editor**: a form over the full canonical field set from `domain/fields.py`, plus an "advanced" section exposing raw/unknown tags from `extra_tags` so nothing in the file is unreachable. Multi-valued fields (artists, genres) get chip inputs, not comma-jammed strings.
- **Bulk editor**: select N tracks in the catalog → edit fields across all of them. Fields with differing values across the selection show a `<multiple values>` state that is preserved unless explicitly overwritten — the classic trap is a bulk editor that silently flattens 40 distinct titles into one.
- **Find & replace across a selection**, with regex opt-in and a live preview of affected values. This is the practical fix for era-specific tagging damage in a mixed-vintage library (e.g. every 2008-era file having `Comment: Ripped by...`).
- **Every manual edit produces a DRAFT ChangeSet** and lands in the same diff review before touching disk. No "quick save" bypass — the safety model has exactly one path through it.

**Grouping workspace (`/groups`)** — specific to this library shape. Shows inferred groups sorted by ascending confidence (worst first, since those need attention), each with its `grouping_basis` badge and "N of M tracks" indicator. Actions: merge groups, split a group, drag tracks between groups, force-to-singleton, and pin a group so rescans never re-guess it. Every action is a previewable ChangeSet.

**Progress: SSE, not WebSockets.** Unidirectional server→client; one endpoint, native `EventSource` auto-reconnect, proxy-friendly. `GET /api/jobs/{id}/events?after=<seq>` replays from `job_events` (that's why it's a table). Worker coalesces to ≤1 event/250 ms per job — else a 40k-file scan writes 40k rows.

**Type safety:** OpenAPI → TS via `openapi-typescript` + `openapi-fetch`, regenerated in CI with a failing diff check. Hand-written API types guarantee drift.

FastAPI mounts the SPA **last**, after `/api` routes, with a catch-all → `index.html` so client-side routing survives refresh. Hashed assets get long `Cache-Control`; `index.html` gets `no-cache`.

### 10. API surface (representative)

```
GET    /api/tracks?q=&missing=art&sort=&cursor=&limit=     cursor pagination throughout
PATCH  /api/tracks/{id}                    → creates DRAFT ChangeSet
POST   /api/groups/{id}/match              {sources} → job or inline candidate list
GET    /api/groups/{id}/candidates         ranked (source, release) rows + dup flags
POST   /api/groups/{id}/stage              {candidate_ref} → ChangeSet from ONE release
GET    /api/changesets/{id}                FieldDiff payload + chosen candidate
PUT    /api/changesets/{id}/candidate      {candidate_ref} → re-stage from a different
                                           release, replacing all proposed values
PATCH  /api/changesets/{id}/changes        bulk decisions + manual value edits
POST   /api/changesets/{id}/apply          → 202 {job_id}
POST   /api/changesets/{id}/undo           → new DRAFT inverse changeset
POST   /api/scan | /api/imports            → job / session
GET    /api/jobs/{id}/events               SSE   |   GET /api/events  SSE global
POST   /api/paths/preview                  {template, album_id} → rendered paths
GET    /api/blobs/{id}?size=thumb          art serving
```

Offset pagination over 100k sorted rows is a trap — **cursor only**. All mutating endpoints accept `Idempotency-Key` (the SPA retries on flaky networks and must not double-apply).

---

### 11. Hardening & release (Phase 7)

Written after Phases 0–6 shipped, so unlike §1–§10 this section is
grounded in the actual tree rather than sketched ahead of it. **Endpoint
signatures, CLI flags and config keys here are normative**, not
illustrative — they were checked against existing code. Where this
section says "already exists," it was verified at commit `d408cc2`.

Phase 7's goal is stated as *"strangers can run it"*. That is the
acceptance test for every item below: a person who has never seen this
repo clones it, runs one command, points it at a real library, and does
not lose data or hit a wall the docs don't cover.

#### 11a. Scope — ten deliverables

The milestone paragraph lists seven items; the drift review
(`docs/DRIFT_REVIEW.md`, run at `3fae00c`) found three more that belong
here because they are hardening gaps rather than new features. All ten
are in scope. **Order matters** — 1–3 are safety, 4–6 are proof, 7–10
are release.

| # | Deliverable | Why it's Phase 7 |
|---|---|---|
| 1 | `--backup` mode | Risk #2's named mitigation; the only one never built |
| 2 | Retention job (journals/blobs/cache) | §4 specifies it; unbuilt, so `apply_journal` grows forever |
| 3 | Structured logging | Prerequisite for diagnosing anything a stranger reports |
| 4 | Playwright E2E | §Testing: *"worth twenty unit tests"* |
| 5 | Hypothesis property tests | §Testing specifies them; **zero exist** (drift review) |
| 6 | 100k-track performance pass | Scale claim in the locked-decisions table is untested |
| 7 | `/api/metrics` | Milestone paragraph |
| 8 | OpenAPI→TS codegen | Risk #8's *sole* mitigation, silently dropped (drift review #1) |
| 9 | Docs + first-try compose | *"strangers can run it"* is mostly this |
| 10 | semantic-release + v1.0.0 | Milestone paragraph |

Explicitly **out of scope**, so nobody pulls them forward: the scoring
corpus expansion (13→50 scenarios — needs real-world data, not a
session's work), WavPack/WMA/DSF fixtures, the beets template-
compatibility suite, and the `/settings` page. Record them in
`docs/PROGRESS.md` as known gaps at v1.0.0 rather than rushing them.

#### 11b. Backup mode

Risk #2 names `--backup` as a mitigation for the project's worst
failure mode. Nothing implements it today.

**Semantics:** before the *first* write to any file in an apply run,
copy the original to a backup root, preserving relative layout. Not
per-changeset — per *file*, once, keyed by content. A file already
backed up (same `content_hash`) is not copied again.

- Config: `storage.backup_dir` (default `None` = disabled) and
  `apply.backup: bool` (default `False`).
- CLI: `muzilla changes apply <id> --backup`.
- API: the existing `POST /api/changesets/{id}/apply` gains an optional
  `{"backup": true}` body field.
- Threading: same explicit-parameter discipline as Phase 5's
  `library_root`/`create_directories` — `apply_changeset()` takes
  `backup_dir: Path | None`, threaded from `WorkerContext.config`.
  **Do not** call `load_config()` inside `changes/`.
- Failure to back up **aborts that file's write** and marks the Change
  `failed`. A backup that silently didn't happen is worse than no
  backup feature at all.

This is `changes/applier.py` — the file §Critical Files calls the
highest-risk code in the project. Extend the existing journal/probe
flow; do not add a second write path beside it.

#### 11c. Retention job

§4 specifies: *journals with `before_blob` kept 30 days / 500
changesets (whichever first), then pruned and marked `undo_expired`*.
Unbuilt. `apply_journal` and the blob store grow without bound.

- New job type `retention_sweep` in `jobs/handlers/`, registered like
  every other handler.
- Prune `apply_journal` rows past either threshold; set the owning
  `ChangeSet.state = "undo_expired"` (**new state value** — check
  `db/models.py`'s state comment and the frontend's changeset-state
  rendering, both need it).
- Release blob refcounts for pruned journals; delete blobs reaching
  refcount 0. `changes/blobstore.py` already has `release()`.
- Sweep `provider_cache` rows past `expires_at`.
- Config: `retention.journal_days` (30), `retention.journal_changesets`
  (500), `retention.enabled` (True).
- Trigger: on worker startup and every 24h. There is no scheduler in
  the codebase — a `asyncio.sleep` loop in the worker pool is
  sufficient and does not justify adding one.
- CLI: `muzilla jobs retention` to run it on demand.

**The undo window must be surfaced, not just implemented** — Risk #2
says "loudly documenting." A changeset whose journals were pruned must
render as undo-unavailable in `ChangeSetReview.tsx` with the reason,
not fail confusingly at undo time.

#### 11d. Structured logging

Today the app uses default uvicorn/print-adjacent logging. Replace with
stdlib `logging` configured for JSON output.

- `src/muzilla/logging.py` (new): `configure_logging(config)` — JSON
  formatter, level from `logging.level` (default `INFO`), `logging.json`
  bool (default `True`; `False` gives human-readable for local dev).
- Every log record carries `job_id` and `change_set_id` when in scope,
  via `contextvars` — set in the job handler wrapper, not passed
  through every call.
- Call it once from `api/app.py`'s lifespan and once from the CLI entry
  point.
- **No secrets in logs.** §8 already mandates `SecretStr`; assert it
  holds by adding a test that configures a provider token and greps the
  emitted records for it.
- Log at boundaries only: job start/end/fail, apply start/end, provider
  request/response status, migration runs. Do not instrument `paths/`
  or `matching/` internals.

#### 11e. Playwright E2E

§Testing: *"Playwright against the real container with providers
stubbed by a local mock server. One test doing scan → match → review →
apply → undo is worth twenty unit tests."*

- Location `e2e/` at repo root (not `tests/` — different runner,
  different deps, must not slow `pytest`).
- `e2e/mock_provider_server.py`: a small FastAPI app replaying the
  committed JSON fixtures already in `tests/fixtures/providers/`.
  Point muzilla at it via the existing provider base-URL config.
  **Reuse those fixtures; do not create a second corpus.**
- The one required test: scan a scratch library → run the grouping
  cascade → match a group → review the changeset → apply → assert tags
  changed on disk → undo → assert bytes are byte-identical to original.
- Second required test: the `/rename` flow end-to-end (Catalog →
  select → Rename → Preview → Stage → Review & apply → files moved →
  Undo → files back). **This page has never been opened in a browser**
  — see `docs/PROGRESS.md`. This test is that verification.
- CI: separate workflow job, `needs: [backend, frontend]`, not blocking
  the fast lint/test loop.
- Auth disabled via `MUZILLA_AUTH__ENABLED=false` in the harness.

#### 11f. Hypothesis property tests

Declared dev dependency since Phase 0, **zero usages**. §Testing
specifies two:

1. **Tag round-trip invariant** —
   `write(read(f) ⊕ changes) → read → assert changes present ∧ everything else unchanged`,
   over the committed format-matrix fixtures. Generate unicode, very
   long strings, empty strings, and multi-valued fields. §Testing says
   this is what catches "the ID3v2.3-vs-2.4 and Vorbis-multi-value bugs
   that otherwise ship."
2. **Template parser fuzzing** — `paths/parser.py` against arbitrary
   input; assert it either returns a `Template` or raises
   `TemplateError` with a valid offset, and **never** any other
   exception type. The lexer/parser is hand-written recursive descent;
   this is exactly what fuzzing is for.

Expect these to find real bugs. If they do, fix the bug — do not narrow
the strategy to make the test pass.

#### 11g. 100k-track performance pass

The locked-decisions table claims ~10k–100k tracks. Never tested above
fixture scale.

- `scripts/gen_perf_library.py` (new, not shipped in the wheel):
  synthesizes N files by copying the committed 1s fixtures and
  retagging each with varied metadata — realistic album/singleton mix
  (~50/50 per the defining constraint), era-varying tag completeness,
  deliberate near-duplicate titles. **~100k × ~18KB ≈ 1.8 GB**; write
  to a scratch path, never inside the repo.
- Measure, and record numbers in `docs/PROGRESS.md`: cold scan, warm
  rescan (§7 claims "seconds" — this is the headline number),
  `GET /api/tracks` first page and deep cursor page, FTS5 search,
  grouping cascade, `/rename` preview over 1k tracks.
- Fix what's slow; **the likely finds are missing indexes and N+1
  queries in the services layer.** `services/paths.py`'s
  `_group_kind()` does a `session.get()` per track inside a loop — that
  one is visible by inspection and will hurt at scale.
- Do not optimize speculatively before measuring.

#### 11h. `/api/metrics`

- Plain-text Prometheus exposition format. **No `prometheus_client`
  dependency** — the metric set is small and hand-formatting avoids a
  dependency for one endpoint.
- Counters: tracks total, tracks missing art / missing album tag,
  changesets by state, jobs by state, provider requests by
  source+outcome, apply successes/failures.
- Cheap queries only — this endpoint will be scraped every 15s. Use
  `COUNT(*)` with existing indexes; no table scans.
- **Unauthenticated by default** but bound behind
  `metrics.enabled` (default `False`), since it exposes library size.
  Document this in the README.

#### 11i. OpenAPI→TS codegen (drift review finding #1)

§9 specifies *"OpenAPI → TS via `openapi-typescript` + `openapi-fetch`,
regenerated in CI with a failing diff check. **Hand-written API types
guarantee drift.**"* Risk #8 names it as the sole mitigation. The tree
hand-writes `frontend/src/lib/types.ts` and has done so for 12 commits.

**Resolve it — do not leave the plan and tree disagreeing.** Preferred:
implement as specified. `npm run generate-types` writing
`frontend/src/lib/api-types.ts` from the live schema, plus a CI step
that regenerates and fails on diff.

Migrate incrementally: generated types are the source of truth for
request/response shapes; hand-written types may remain for UI-only
view-models. If after attempting it the migration proves genuinely
disproportionate, **record that decision in PLAN.md and Risk #8** —
the unacceptable outcome is a third phase of silent divergence.

#### 11j. Docs + first-try compose

*"Strangers can run it"* is mostly this.

- README: what it is, what it deliberately is not (no playback, no
  library management), screenshots, quickstart, config reference,
  **the undo retention window**, and a plain warning that it writes to
  audio files.
- `docker-compose.yml`: works with zero edits beyond a password.
  Verify by `docker compose up` from a **fresh clone in a temp dir** —
  gotchas 10–12 in `docs/PROGRESS.md` are all compose/Docker traps that
  only surfaced this way.
- `CONTRIBUTING.md`: the verification gate from `CLAUDE.md`, the
  layering contract, conventional commits.
- **Fix the `audio` extra gap**: CI installs `.[dev]` only, so
  `pillow`/`pyacoustid` are absent; art and fingerprint code paths are
  therefore untested in CI. Install `.[dev,audio]`. This was hit live
  on a fresh checkout — `from PIL import Image` fails on a
  documented-setup-following install.

#### 11k. semantic-release and v1.0.0

- Conventional commits are already the convention, so
  `python-semantic-release` can derive versions from history.
- Configure in `pyproject.toml`; version lives in
  `src/muzilla/__about__.py` (already exists).
- Release workflow: on push to `main`, compute the version, update the
  changelog, tag. The existing `publish.yml` already triggers on
  `v*.*.*` tags and needs no change.
- **Tagging v1.0.0 is a human decision, not an automated one.** It is
  irreversible-ish (GHCR publish, public release) and gated on every
  item above being genuinely done — including E2E green and the
  performance numbers recorded. An implementing session should wire the
  tooling and then **stop and ask** before the first release runs.

#### 11l. Acceptance checklist for v1.0.0

Do not tag until all of these are true:

- [ ] `--backup` copies originals and refuses to write when backup fails
- [ ] Retention job prunes journals and marks `undo_expired`; UI shows it
- [ ] JSON logs carry `job_id`/`change_set_id`; no secret appears in logs
- [ ] Both E2E tests pass against the real container
- [ ] Property tests pass; any bug they found is fixed, not silenced
- [ ] 100k numbers recorded in PROGRESS.md; warm rescan is seconds
- [ ] `/api/metrics` scrapes without a table scan
- [ ] Codegen wired **or** an explicit decision recorded in PLAN.md
- [ ] `docker compose up` works from a fresh clone in a temp dir
- [ ] CI installs `.[dev,audio]`
- [ ] `docs/PROGRESS.md` lists the out-of-scope gaps as known at v1.0.0

---

## Features beets lacks that muzilla adds

1. **Multi-source candidates in one ranked list** — beets queries several sources too, but muzilla shows MusicBrainz, Discogs and Deezer candidates side by side with duplicate-alternatives flagged, so choosing between them is a single visual comparison rather than a config change and a re-run. Picard is MusicBrainz-only. *(Per-field cross-source merging was considered and deliberately rejected — see §3.)*
2. **True undo** with before-state snapshots
3. **Dry-run by default, always** — including renames
4. **Library health dashboard** — missing art / no ReplayGain / absent lyrics / inconsistent album artists / duplicates
5. **Saved rules & profiles** — "for jazz: prefer Discogs genres, strip comments, template X" without editing YAML
6. **Tag hygiene linter** — trailing whitespace, `feat.` inconsistency, "The Beatles" vs "Beatles", mixed date formats, with bulk fix
7. **[LRCLIB](https://lrclib.net) synced lyrics** — free, no auth, ~3M tracks, real `.lrc` timed output (beets' lyrics plugin is plaintext and scraper-fragile)
8. **Duplicate detection by fingerprint**, not filename (Phase 6)

---

## Native dependencies

| Need | Library | Native? | Notes |
|---|---|---|---|
| Tag I/O | `mutagen` | No | Pure Python |
| ReplayGain | **`rsgain`** | **Yes** | Small C++ binary, EBU R128 + true peak + album gain. `bs1770gain` is abandoned. Not in Ubuntu repos → install pinned `.deb` from GitHub releases with checksum |
| Fingerprinting *(Ph6)* | `pyacoustid` + **`fpcalc`** | **Yes** | `libchromaprint-tools`, ~2 MB |
| Fuzzy match | `rapidfuzz` | No (wheels) | |
| Assignment | `scipy` | No (wheels) | ~35 MB |
| Images | `Pillow` | No (wheels) | Art resize/validate/thumbnail |
| HTTP | `httpx` + `hishel` | No | |
| Lyrics | direct LRCLIB | No | ~60 lines; avoids deps bundling scrapers of dubious legality |
| Auth | `argon2-cffi` | No (wheels) | |

**Image size:** python:3.12-slim + ffmpeg + rsgain + scipy ≈ **~550 MB** (dropping librosa saves ~700 MB). Multi-stage build, `--no-install-recommends`.

---

## Phased Milestones

Each phase ends in something runnable and independently useful.

**Phase 0 — Skeleton + design system port** *(runnable: `muzilla --version`, `docker compose up` serves health; component gallery renders)*
Repo + `git init`, MIT license, pyproject, ruff + mypy strict + pytest + import-linter, pre-commit, conventional commits, GitHub Actions CI, GHCR publishing workflow, `domain/fields.py` registry, config loader, engine + WAL + Alembic baseline, FastAPI `/api/health`, Typer skeleton, multi-stage Dockerfile, compose, README, CONTRIBUTING.

**Plus the design-system port** (§9): Vite + React + TS + Tailwind scaffold, all five token CSS files ported verbatim, IBM Plex self-hosted, the nine UI components converted to `.tsx`, and a Storybook (or a simple `/dev/components` route) rendering every component in all states across both themes. Doing this in Phase 0 means every later screen composes existing, reviewed primitives instead of re-deriving styling from mockups — and it's the cheapest possible check that the tokens actually work in the real stack.

**Phase 1 — Read-only catalog + library analysis** *(useful: understand what you actually have)*
mutagen reader + full format-matrix mapping, scan pipeline, `tracks`/`track_groups` + FTS5, catalog service + endpoints, `muzilla scan`, React app with virtualized table, auth. **No writes anywhere** — safe to point at the real library. Ships the format-matrix test suite.

Plus a **library analysis report** (`muzilla analyze`, and a dashboard panel): actual album/single split, tag completeness by field, how many files have usable album tags, duplicate candidates, format/bitrate breakdown. In a flat folder with era-varying tags, *nobody knows what's really in there* — including you. This report drives the Phase 2 grouping tuning and is cheap to build on top of the scan.

**Phase 2 — Staged changes + manual editing + grouping** *(useful: a safe tag editor with undo)*
Full `changes/` package: builder, differ, applier with journal, undo, conflict detection, blob store. Strip rules. Changeset API + **the diff review UI**. Free-form manual tag editing (single + bulk). `muzilla edit`, `changes apply/undo`.

Also the **grouping cascade** (§7b) minus its fingerprint stage, plus the **grouping correction UI** (merge/split/reassign/pin). Grouping ships here rather than Phase 1 because a correction *is* a ChangeSet and needs the changeset machinery underneath it. First phase that writes to disk — **deliberately sequenced so the safety net exists before the first write**.

**Phase 3 — Providers + matching + fingerprinting** *(useful: automatic metadata from three sources)*
Protocols, HTTP client, rate limiter, both caches, MusicBrainz + Deezer + Discogs + Cover Art Archive. Distance, string distance, Hungarian alignment, and **multi-source candidate ranking with duplicate flagging — one release per candidate, no field-level merging** (§3). **Both matching paths**: release-level for albums, recording-level for singletons. Candidate-picker UI, and re-staging a changeset when a different candidate is chosen.

**AcoustID fingerprinting lands here, on by default** (moved from Phase 6): `fpcalc` in the image, batched lookups, the fingerprint short-circuit, and fingerprint-based grouping (Stage 2 of the cascade). **Scoring regression corpus established here and maintained forever — with separate album and singleton corpora**, since they're different problems with different failure modes.

**Phase 4 — Jobs + import pipeline** *(useful: bulk-import a whole library from the browser)*
Job table, queue, asyncio worker, SSE, cancellation. Resumable import session state machine + review inbox. Import wizard, jobs page. Startup crash recovery for both jobs and the apply journal.

> **Implementation-order note** (recorded so a fresh session isn't
> confused by execution not matching the step list literally): the
> plan session that built this phase converted `apply`/`undo` to job
> types as part of the queue/worker work (rather than deferring it),
> which meant `POST /api/changesets/{id}/apply|undo` needed something
> pollable to stay testable end-to-end. Rather than fake that with a
> synchronous test-only shortcut, a minimal `GET/POST /api/jobs`
> surface (list/detail/cancel — no SSE yet) was pulled forward out of
> what was originally scoped as a single later "API routers" step. SSE
> and the `/api/imports` endpoints still land as their own step. This
> is a within-phase reordering for testability, not a scope change —
> don't re-derive or re-plan it in a future session, just be aware the
> jobs router already partially exists when picking this phase back up.

**Phase 5 — Path templates + renaming** *(useful: consistent filenames now, full reorganization whenever you want)*
Lexer/parser/compiler, function library, `%aunique` with the projected-end-state resolver, sanitization, library-wide collision detection. **Both rendering modes built and tested here** (see §6): flat filename-only as the shipped default, plus directory-creating mode with `mkdir -p`, empty-source pruning, and the file-tree preview. Query-keyed template overrides (`"genre:Classical": ...`) like beets. Rename as a Change kind. `muzilla path-test`, live preview endpoint, template editor in settings with validation and sample output. **High blast radius, so it lands after the changeset/undo machinery is battle-tested.**

Building foldering now rather than later costs little (the engine already renders full paths — the extra work is `mkdir`, pruning, and the tree preview) and avoids a retrofit if the library is ever reorganized.

**Phase 6 — Enrichment** *(useful: complete, not just correct, metadata)*
ReplayGain via rsgain; album art fetch/embed/resize (**embedded primarily** — one flat folder can hold only one `cover.jpg`) with the art diff UI; LRCLIB lyrics; **fingerprint-based duplicate detection** (finding the same track at different bitrates, which a flat folder full of mixed-era rips will have plenty of). Each a separate job type. *(Fingerprinting itself already shipped in Phase 3.)*

**Phase 7 — Hardening & release** *(useful: strangers can run it)*
Backup mode, cache/undo retention jobs, Playwright E2E, performance pass on a 100k-track library, structured logging, `/api/metrics`, semantic-release, docs, a compose file that works first try. Tag **v1.0.0**.

> **See §11 for the full specification.** This paragraph is a summary;
> §11 is the implementable spec — ten deliverables (the seven above plus
> three hardening gaps the drift review found), each with config keys,
> endpoint signatures, file locations and failure semantics, an explicit
> out-of-scope list, and a v1.0.0 acceptance checklist. §11 was written
> against the actual tree rather than ahead of it, so unlike §1–§10 its
> signatures are **normative**.

**Optional Phase 8** — plugin API (entry-point Protocol registration, *not* an event bus), more providers (Beatport, fanart.tv, Genius), beets-import compatibility, BPM/key as `muzilla:full`.

---

## Testing Strategy

**Tag round-trips without real music.** Commit ~15 minimal silent fixture files (~200 KB total) rather than synthesizing with ffmpeg — CI needs no ffmpeg, and fixtures never change. Property-test with Hypothesis: `write(read(f) ⊕ changes) → read → assert changes present ∧ everything else unchanged`, generating unicode, very long strings, and multi-valued fields. Catches the ID3v2.3-vs-2.4 and Vorbis-multi-value bugs that otherwise ship. Format matrix (MP3 v2.3+v2.4, FLAC, Ogg, Opus, M4A, WavPack, WAV, AIFF, WMA, DSF) as a parametrized fixture visible in CI output.

**Providers, three tiers:**
1. **Unit** — normalization fed committed JSON fixtures (`tests/fixtures/providers/musicbrainz/release_<mbid>.json`). No HTTP. ~80% of provider tests.
2. **Transport** — `respx` for rate limiting, retry, 429, ETag revalidation, auth headers. Fake clock, never `sleep`.
3. **Contract** — VCR cassettes, `@pytest.mark.network`, excluded by default, run on a **weekly scheduled CI job** so API drift is found before users report it.

A global autouse fixture patches the httpx transport to **raise on any unmocked request**.

**Matching** — ~50 real-world album scenarios as YAML (local tags + candidates + expected winner), run as a scoring regression reporting top-1 accuracy. When someone tunes a weight, this shows immediately what broke. **The highest-value test asset in the project.**

**Apply/undo** — `tmp_path` fixture copies; test conflict detection by mutating a file between stage and apply; test crash recovery by injecting an exception mid-journal and asserting restoration.

**Path templates** — table-driven, porting beets' own template test cases as a compatibility suite, plus Hypothesis fuzzing the parser.

**E2E** — Playwright against the real container with providers stubbed by a local mock server. One test doing scan → match → review → apply → undo is worth twenty unit tests.

---

## Verification

Per phase:
```bash
# Backend
uv run pytest -q                       # unit + integration
uv run pytest -m network               # contract tests (manual/weekly)
uv run ruff check . && uv run mypy src && uv run lint-imports   # layering contract

# Frontend
cd frontend && npm run typecheck && npm run build

# Container — the real acceptance test
docker compose up --build
curl -f localhost:8080/api/health
```

**Phase 0 design-port acceptance** — the component gallery at `/dev/components` must render every one of the nine components in all documented states, in **both** themes (toggle `data-theme="light"` on `<html>`), with:
- no unresolved CSS variables (spot-check computed styles; a typo'd `var(--foo)` silently renders as nothing)
- IBM Plex loading from the local bundle with **no network requests to fonts.googleapis.com** (verify offline, since the mini-PC may have no internet)
- diff colors passing WCAG AA against their paired `-bg` tints in both themes
- `Badge` rendering its `dot` in every tone — the colorblind-redundancy contract

End-to-end smoke on a **copy** of real music:
```bash
muzilla scan ~/music-test
muzilla match album <id>               # inspect diff in terminal
muzilla changes apply <changeset-id>
muzilla changes undo <changeset-id>    # verify files byte-identical to pre-apply
```
Then the same flow through the browser, confirming the diff UI, SSE progress, and undo.

---

## Genuine Risks

0. **★ Grouping is the highest-leverage error class in a flat library.** A wrong group produces a wrong release match, which writes wrong tags to a dozen files at once. With no directory signal and era-varying tag quality, muzilla is genuinely guessing — so the design makes guesses *visible* (confidence + basis on every group), *cheap to fix* (merge/split/pin), and *sticky* (pins survive rescans). Do not attempt to be silently clever here; surface the uncertainty. This risk did not exist in the first draft of the plan, which assumed directories were reliable.
1. **Matching quality *is* the product.** Wrong releases make everything else irrelevant. Mitigation: the scoring corpus from Phase 3 onward, conservative auto-apply thresholds, and an always-visible chosen-release badge so it is never unclear *which* release is being applied. Budget disproportionate time here. **Singleton matching is the riskier half** — fewer corroborating signals means confident-looking wrong matches, hence the stricter 0.06 threshold and the earliest-release preference to avoid crediting everything to compilations.
2. **Data loss on apply** — users point this at irreplaceable libraries. Mitigations: journal + before-blobs, dry-run default, `--backup` mode copying originals before first write, drift detection, and **loudly documenting the undo retention window**.
3. **Rate limits make bulk import slow** — MB at 1 req/s means a 1000-album import takes ≥17 min of pure MB time. Mitigate with aggressive caching, batched AcoustID, Deezer/Discogs pre-filtering, and honest UI estimates. Never let users think it hung.
4. **Discogs data quality is uneven** (user-submitted, inconsistent genre vocabularies, near-duplicate pressings). Don't weight it equally with MB for canonical fields; do use it for label/catalog/pressing where it genuinely excels.
5. **SQLite write contention** if the design drifts to multiple writers — enforce single-writer from day one.
6. **In-process worker: a crashed job can take down the API.** Wrap every handler in a supervisor, run in a thread pool with timeouts, hard-kill subprocesses on cancel, and bound concurrency **well below CPU count** on a mini-PC or the UI stalls during import.
7. **Unicode/filesystem interactions** (NFC vs NFD, SMB/exFAT restrictions, case-insensitive collisions) will bite on a mini-PC serving over SMB. Handle in `sanitize.py` from the start. **Amplified by the flat layout**: one directory holding 10k–100k files means filename collisions are common rather than rare, and some filesystems degrade badly on very large single directories.
8. **Frontend/backend type drift** — mitigated by CI-generated OpenAPI client with a failing diff check.

---

## Critical Files

- `src/muzilla/domain/fields.py` — canonical field registry; **write this first**, everything derives from it
- `src/muzilla/db/models.py` — the `change_sets`/`changes`/`apply_journal` trio is the product's spine
- `src/muzilla/changes/applier.py` — journaled, conflict-checked, atomic-replace writes; **highest-risk code in the project**
- `src/muzilla/matching/engine.py` — multi-source scoring and merge; determines whether the product is good
- `src/muzilla/providers/base.py` — the Protocols keeping six heterogeneous APIs uniform
- `frontend/src/pages/ChangeSetReview.tsx` — the diff review screen the whole design exists to serve
