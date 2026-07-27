# muzilla

A self-hosted music **metadata** manager — like [beets](https://github.com/beetbox/beets), but scoped deliberately to metadata: tags, album art, lyrics, genres, ReplayGain, and acoustic fingerprints. Unlike [MusicBrainz Picard](https://picard.musicbrainz.org/), muzilla fetches from multiple sources at once (MusicBrainz, Discogs, Deezer) and ranks them as complete releases, not per-field merges. Unlike beets, it ships a real browser GUI, not just a CLI.

**What it deliberately is not:** muzilla never plays audio, never manages a listening library (no playlists, no "now playing"), and never reorganizes your files beyond a filename rename you explicitly review and apply. It reads tags, proposes better ones, shows you a field-level diff, and writes only when you accept — every write is undoable.

**⚠️ It writes to your audio files.** Every apply is journaled and reversible through `undo`, but back up anything irreplaceable before pointing muzilla at a real library, and read [Undo and the retention window](#undo-and-the-retention-window) below before relying on undo past a few weeks.

## Status

Phases 0–6 complete (catalog, staged edits with undo, multi-source matching, background jobs/import, path renaming, ReplayGain/art/lyrics/duplicate-detection enrichment). Phase 7 (hardening: backup mode, crash-safe retention, structured logs, metrics, performance-tested to 100k tracks) is in progress. See [`docs/PROGRESS.md`](docs/PROGRESS.md) for what's built and [`docs/PLAN.md`](docs/PLAN.md) — gitignored, local only — for the full architecture.

## Core idea

Browse your catalog → select tracks → fetch metadata or edit manually → review a field-level diff → accept → apply (undoable). For a whole library at once: scan → let the grouping cascade infer albums/singletons from tags and fingerprints (no reliance on folder structure) → match each group against MusicBrainz/Discogs/Deezer → review → apply.

## Quickstart (Docker)

```bash
git clone <this-repo>
cd muzilla
cp .env.example .env   # set MUZILLA_AUTH__PASSWORD and MUZILLA_AUTH__SESSION_SECRET
MUZILLA_LIBRARY_PATH=/path/to/your/music docker compose up --build
```

Then open <http://127.0.0.1:8080>. The container listens on localhost only by default — see the comment in `docker-compose.yml` before exposing it further.

## Configuration reference

Config layers, lowest to highest priority: packaged defaults (`src/muzilla/config/defaults.yaml`) → `/etc/muzilla/config.yaml` → `$MUZILLA_CONFIG_DIR/config.yaml` → `--config` file → `MUZILLA_*` environment variables. Every config key can be set via env var with `__` as the nesting separator, e.g. `paths.create_directories` → `MUZILLA_PATHS__CREATE_DIRECTORIES`.

Notable settings (see `defaults.yaml` for the full set with inline docs):

| Key | Env var | Default | What it does |
|---|---|---|---|
| `auth.enabled` | `MUZILLA_AUTH__ENABLED` | `true` | Single-password session auth. Refuses to start if enabled with no password set. |
| `storage.library_root` | `MUZILLA_STORAGE__LIBRARY_ROOT` | `/music` | Where your audio files live. |
| `storage.backup_dir` | `MUZILLA_STORAGE__BACKUP_DIR` | unset | If set, `apply --backup` copies each file's original here before its first write. |
| `paths.create_directories` | `MUZILLA_PATHS__CREATE_DIRECTORIES` | `false` | Rename mode: flat filenames only (default) vs. creating subdirectories. |
| `retention.journal_days` / `retention.journal_changesets` | `MUZILLA_RETENTION__*` | `30` / `500` | See the retention window section below. |
| `metrics.enabled` | `MUZILLA_METRICS__ENABLED` | `false` | Exposes `GET /api/metrics` (Prometheus format, unauthenticated) — reveals library size, so opt-in. |

## Undo and the retention window

Every apply is journaled before it writes, so `muzilla changes undo <id>` (or the Undo button) can revert it — including a rename, and including undo-of-undo (redo). But the journal isn't kept forever: a background sweep prunes journal rows once **either** 30 days have passed **or** the 500 most-recently-touched changesets have accumulated (whichever comes first — both configurable, see the table above). Once a changeset's journal is pruned it's marked `undo_expired` and can no longer be undone through the app — the tag/file changes themselves are untouched, only the ability to revert them through muzilla is gone. If you need a change reversible indefinitely, keep your own backup (`storage.backup_dir` + `apply --backup`) rather than relying on the journal.

## Development

Backend:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,audio]"
muzilla serve --reload
```

`[audio]` is required, not optional, despite the name — it pulls in `pillow` (album art) and `pyacoustid` (fingerprinting), both on real code paths, not just the fingerprint-specific one. `.[dev]` alone will `ModuleNotFoundError` on `PIL` the first time an art-embedding path runs.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full verification gate, project layout, and commit conventions.

## Stack

Python 3.12 / FastAPI / SQLite core, with a Typer CLI and a React GUI served from the same container. See `docs/PLAN.md` for the full rationale.

## License

MIT — see [LICENSE](LICENSE).
