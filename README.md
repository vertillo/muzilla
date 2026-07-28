# muzilla

A self-hosted music **metadata** manager — like [beets](https://github.com/beetbox/beets), but scoped deliberately to metadata: tags, album art, lyrics, genres, ReplayGain, and acoustic fingerprints. Unlike [MusicBrainz Picard](https://picard.musicbrainz.org/), muzilla fetches from multiple sources at once (MusicBrainz, Discogs, Deezer) and ranks them as complete releases, not per-field merges. Unlike beets, it ships a real browser GUI, not just a CLI.

**What it deliberately is not:** muzilla never plays audio, never manages a listening library (no playlists, no "now playing"), and never reorganizes your files beyond a filename rename you explicitly review and apply. It reads tags, proposes better ones, shows you a field-level diff, and writes only when you accept — every write is undoable.

**⚠️ It writes to your audio files.** Every apply is journaled and reversible through `undo`, but back up anything irreplaceable before pointing muzilla at a real library, and read [Undo and the retention window](#undo-and-the-retention-window) below before relying on undo past a few weeks.

## Status

Phases 0–7 complete: catalog, staged edits with undo, multi-source matching, background jobs/import, path renaming, ReplayGain/art/lyrics/duplicate-detection enrichment, and hardening (backup mode, crash-safe retention, structured logs, metrics, 100k-track performance pass). Phase 8 (Docker deployment, security hardening, resource budget, application shell and screen-flow fixes) is in progress — see `docs/PLAN.md` §12 for the full breakdown and [`docs/PROGRESS.md`](docs/PROGRESS.md) for gotchas and decisions.

## Core idea

Browse your catalog → select tracks → fetch metadata or edit manually → review a field-level diff → accept → apply (undoable). For a whole library at once: scan → let the grouping cascade infer albums/singletons from tags and fingerprints (no reliance on folder structure) → match each group against MusicBrainz/Discogs/Deezer → review → apply.

## Quickstart (Docker)

```bash
git clone <this-repo>
cd muzilla
cp .env.example .env   # set MUZILLA_AUTH__PASSWORD and MUZILLA_AUTH__SESSION_SECRET
MUZILLA_LIBRARY_PATH=/path/to/your/music docker compose up --build
```

Then open <http://127.0.0.1:1846>. The container is reachable from your LAN by default — see [Deploying on a home server](#deploying-on-a-home-server) below before exposing it further.

## Deploying on a home server

This section targets running muzilla unattended on a machine like an Ubuntu mini PC, not local development — for that, see [Development](#development) below.

### Prerequisites

Docker Engine plus the Compose plugin (`docker compose`, not the old standalone `docker-compose` v1 — v1 does not understand this project's compose file and will not work):

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER   # log out and back in for this to take effect
docker compose version          # confirm the plugin, not just the daemon
```

### First run

```bash
git clone <this-repo>
cd muzilla
cp .env.example .env
```

Generate both secrets and paste them into `.env`:

```bash
openssl rand -base64 36   # MUZILLA_AUTH__PASSWORD
openssl rand -base64 36   # MUZILLA_AUTH__SESSION_SECRET
```

Set `MUZILLA_LIBRARY_PATH` in `.env` to the absolute path of your music library, then:

```bash
docker compose up -d --build
```

### Where data lives

- The `muzilla-data` named Docker volume holds the SQLite database, blob storage (embedded art, journal state) and backups. Back this up — it's the entire catalog and undo history.
- `/music` is a bind mount of `MUZILLA_LIBRARY_PATH` — **muzilla writes to these files directly** once you apply a change. Back up your library the same way you would before running Picard or beets against it.

### File ownership

The container runs as uid 1000. If your music library is owned by a different user, tag writes will fail with a permission error the first time you apply a change.

```bash
id -u   # check your own uid
```

Either make the library readable/writable by uid 1000:

```bash
sudo chown -R 1000:1000 /path/to/your/music
```

or override the container's user to match yours, in `docker-compose.yml`:

```yaml
    user: "1001:1001"   # your actual uid:gid
```

### LAN access

Once the container is healthy, muzilla is reachable at `http://<mini-pc-ip>:1846` from any device on the same network.

### Remote access

> ⚠️ **Do not expose muzilla through Cloudflare Tunnel or Tailscale Funnel yet.** Auth is mandatory and enforced (`MUZILLA_AUTH__PASSWORD` must be set or the app refuses to start), but that alone is not enough to put in front of the public internet: `docs/PLAN.md` §12c (in progress) closes an unauthenticated file-read path and adds login rate limiting, and both matter far more once this service is internet-reachable than while it's LAN-only. The setup below is documented for when that phase lands — treat it as reference, not a green light. Pick one of the following rather than forwarding port 1846 on your router once it does.

#### Cloudflare Tunnel

Point `cloudflared` at the container's published port, not its internal one:

```bash
cloudflared tunnel --url http://localhost:1846
```

Cloudflare terminates TLS, so the browser sees HTTPS even though muzilla itself is still speaking plain HTTP locally. **Set `MUZILLA_AUTH__COOKIE_SECURE=true`** in `.env` before going live — without it, the session cookie can still be sent in the clear over the plain-HTTP LAN hop between the tunnel and the container.

Put [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/policies/access/) in front of the tunnel if you can. muzilla's login form is a reasonable second factor, not a reasonable only factor, for a tool that rewrites your library.

#### Tailscale

```bash
tailscale serve --bg 1846        # tailnet-only
tailscale funnel --bg 1846       # public internet, via your tailnet
```

On a tailnet, prefer `MUZILLA_BIND_ADDRESS=127.0.0.1` in `.env` — Tailscale's own proxy reaches the container over loopback, so there's no reason to also leave port 1846 open on the LAN interface.

#### Either way: the proxy hides real client IPs

Behind a tunnel, every request reaches muzilla from `127.0.0.1`, not the real client, which makes access logs useless and collapses the login rate limiter into one shared bucket for every visitor. `muzilla serve` trusts `X-Forwarded-For`/`X-Forwarded-Proto` only from the addresses named by `--forwarded-allow-ips` (default `127.0.0.1`, matching a same-host tunnel/proxy — this is already the container's default via `docker/Dockerfile`'s `CMD`). **Never set this to `*`** — trusting forwarded headers from an untrusted source lets any client spoof its IP and bypass the rate limiter entirely. If your reverse proxy runs on a different host, override the flag (or `docker-compose.yml`'s `command:`) to name that host's address specifically, not a wildcard.

#### Why there's no HSTS header

muzilla deliberately does not send `Strict-Transport-Security`, even behind a TLS-terminating tunnel. HSTS is a browser-side, origin-wide instruction — it would tell your browser to refuse **any** future plain-HTTP connection to muzilla's hostname, including the LAN path this same deployment relies on when you're not going through the tunnel. If you want HSTS, terminate it at the tunnel/proxy layer, not the application.

### Upgrading

```bash
git pull
docker compose up -d --build
```

**Back up the `muzilla-data` volume first.** Migrations run automatically at startup (`api/app.py`'s lifespan calls `run_migrations`) — there is no manual migration step, but that also means there's no prompt before schema changes apply.

### Resource expectations

<!-- TODO(step-3.4) -->

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
| `metrics.enabled` | `MUZILLA_METRICS__ENABLED` | `false` | Exposes `GET /api/metrics` (Prometheus format, unauthenticated) — reveals library size, so opt-in, and never route it through a tunnel. |

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

The dev server proxies `/api` to `http://localhost:8080`, matching `muzilla serve`'s default port — unrelated to the Docker container's published port 1846.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full verification gate, project layout, and commit conventions.

## Stack

Python 3.12 / FastAPI / SQLite core, with a Typer CLI and a React GUI served from the same container. See `docs/PLAN.md` for the full rationale.

## License

MIT — see [LICENSE](LICENSE).
