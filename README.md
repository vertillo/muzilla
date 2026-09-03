# muzilla

A self-hosted music **metadata** manager — like [beets](https://github.com/beetbox/beets), but scoped deliberately to metadata: tags, album art, lyrics, genres, ReplayGain, and acoustic fingerprints. Unlike [MusicBrainz Picard](https://picard.musicbrainz.org/), muzilla fetches from multiple sources at once (MusicBrainz, Discogs, Deezer) and ranks them as complete releases, not per-field merges. Unlike beets, it ships a real browser GUI, not just a CLI.

**What it deliberately is not:** muzilla never plays audio, never manages a listening library (no playlists, no "now playing"), and never reorganizes your files beyond a filename rename you explicitly review and apply. It reads tags, proposes better ones, shows you a field-level diff, and writes only when you accept — every applied write is journaled, with undo available while its retained recovery state remains valid and no file drift or collision blocks recovery.

**⚠️ It writes to your audio files.** Every apply is journaled; undo is available while its retained recovery state remains valid and file drift or collisions do not block it. Back up anything irreplaceable before pointing muzilla at a real library, and read [Undo and the retention window](#undo-and-the-retention-window) below before relying on undo past a few weeks.

## Status

Muzilla is not yet ready for normal use on an irreplaceable library. The core ReviewBundle,
journaled apply/undo, provider, reset, migration and container paths are implemented, but
known completion work remains. The web UI is the primary interface; the CLI is support and
troubleshooting tooling and never applies metadata or file changes. There is no auto-apply in
any mode: a strong match is only preselected for review, and changes land only when you click
Apply in the web UI. Do not infer production readiness from a green health endpoint or from
historical test results.

The current sources of truth are the [product specification](docs/product-spec.md),
[completion matrix](docs/completion-matrix.md), and
[production-readiness contract](docs/production-readiness.md).

## Core idea

Select files or folders → scan tags and filenames → retrieve and rank candidates → prepare
metadata, filename/path, cover, lyrics and ReplayGain → review them in one ReviewBundle →
apply through the journaled file writer → update the catalog. Technical tasks remain isolated
and retryable while the user sees one coherent review.

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

The SPA route is contained to its static root, login is rate-limited, security headers and a strict CSP are set, sessions are revoked on logout, scan/import paths are constrained to the configured library root, and proxy headers are trusted only from explicitly named addresses. Auth is mandatory (`MUZILLA_AUTH__PASSWORD` must be set or the app refuses to start). Before exposing the service, also use a trusted access layer, set `MUZILLA_AUTH__COOKIE_SECURE=true`, and scope `--forwarded-allow-ips` correctly. Pick one of the following rather than forwarding port 1846 on your router.

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

### Cold backup and restore of `/data`

The supported operator backup is a cold archive of the `muzilla-data` volume. Stop the
Compose project first, archive `/data` from the stopped volume to storage outside Docker,
and record a SHA-256 checksum alongside the archive. Restore only into a newly created,
empty destination volume after verifying that checksum. Start the exact image that was
validated for the backup (the container image reference must match); do not restore into a
volume that already contains state. The music bind mount (`/music`) and operator-owned
configuration or secrets outside `/data` are intentionally excluded and must be backed up
separately. The deterministic Compose smoke in `tests/container/backup_restore_smoke.py`
exercises this contract, including exact-image validation and persistence after recreate.

Example operator sequence (replace the image and volume names with the values in your
deployment):

```bash
docker compose stop
docker run --rm --mount type=volume,src=muzilla-data,dst=/data,readonly \
  --mount type=bind,src="$PWD/backup",dst=/backup alpine:3.21 sh -ec \
  'tar -C /data -cf /backup/muzilla-data.tar . && sha256sum /backup/muzilla-data.tar > /backup/muzilla-data.tar.sha256'
# Copy both files to external storage and verify the checksum before restore.
docker volume create muzilla-data-restore
sha256sum -c backup/muzilla-data.tar.sha256
docker run --rm --mount type=volume,src=muzilla-data-restore,dst=/data \
  --mount type=bind,src="$PWD/backup",dst=/backup,readonly alpine:3.21 sh -ec \
  'test -z "$(find /data -mindepth 1 -print -quit)" && tar -C /data -xf /backup/muzilla-data.tar'
```

Before starting, point Compose at the validated image reference and the restored volume;
never overwrite a non-empty destination. This procedure restores Muzilla state only — it
does not restore music files, `.env`/config files, or provider credentials supplied outside
`/data`.

### Safe reset

Settings has two separate Danger zone actions. Both acquire a persistent maintenance
lock, wait for mutating HTTP requests already in flight, cancel/quiesce the worker, clear
only allow-listed Muzilla state and then restart the worker. The API requires a
same-origin request, a session-bound CSRF token and a persistent `Idempotency-Key`.

- **Reset catalog and activity** removes the indexed catalog, reviews, jobs, provider
  cache and managed art blobs. It preserves Settings overrides, provider credentials,
  auth sessions, bootstrap configuration and backups.
- **Factory reset** additionally removes Settings overrides and every provider credential
  managed by Muzilla. It requires the current password plus the exact phrase shown in the
  dialog and revokes all sessions, so the next action requires login again.

Neither action traverses, deletes, renames or rewrites `storage.library_root`; configured
backups are also outside reset ownership. Muzilla refuses the reset before its first
delete if cache/blob/secret roots overlap the library, backup directory or database, or
if a managed root is a symlink/non-directory. Bootstrap auth/storage values supplied by
environment or config files are operator-owned and intentionally survive factory reset.

If the process stops after DB cleanup but before cache/blob/secret cleanup, the persisted
maintenance lock prevents enqueue/apply. Startup completes the authorized cleanup before
provider clients or workers start; if that cannot be done safely, startup fails closed.
Do not use reset as a substitute for backups or migrations.

The supported primary views are Dashboard, Catalog, Reviews, Activity and Settings; Import
is a supported journey launched from the web UI. The old Groups, Jobs and Duplicates SPA
routes have no dedicated page or workflow redirect (an unmatched old URL lands on Dashboard
via the router wildcard): their workflows now live in a review, Activity and Catalog
respectively. Legacy ChangeSet
links and interfaces still exist as a compatibility surface tracked in the completion matrix;
the target has no permanent ChangeSet compatibility requirement and does not preserve
pre-production ChangeSet application state. Metadata/file Apply is exclusively a web UI
action; the current `muzilla changes apply` CLI command is part of that legacy surface and is
removed with it.

## Configuration reference

Config layers, lowest to highest priority: packaged defaults (the Pydantic field defaults in `src/muzilla/config/schema.py`) → `/etc/muzilla/config.yaml` → `$MUZILLA_CONFIG_DIR/config.yaml` → `MUZILLA_*` environment variables. Every config key can be set via env var with `__` as the nesting separator, e.g. `paths.create_directories` → `MUZILLA_PATHS__CREATE_DIRECTORIES`.

Notable settings (see `src/muzilla/config/schema.py` for the full set with field defaults):

| Key | Env var | Default | What it does |
| --- | --- | --- | --- |
| `auth.enabled` | `MUZILLA_AUTH__ENABLED` | `true` | Single-password session auth. Refuses to start if enabled with no password set. |
| `storage.library_root` | `MUZILLA_STORAGE__LIBRARY_ROOT` | `/music` | Where your audio files live. |
| `storage.data_dir` | `MUZILLA_STORAGE__DATA_DIR` | `/data` | Root for runtime state. |
| `storage.db_path` | `MUZILLA_STORAGE__DB_PATH` | `/data/muzilla.db` | The SQLite index. Independent of `data_dir` — overriding `data_dir` alone does not move it. |
| `storage.cache_dir` | `MUZILLA_STORAGE__CACHE_DIR` | `/data/cache` | Provider HTTP cache. Safe to delete. |
| `storage.blob_dir` | `MUZILLA_STORAGE__BLOB_DIR` | `/data/blobs` | Content-addressed art backing live undo/apply-journal state. **Not** safe to delete. |
| `storage.provider_secrets_dir` | `MUZILLA_STORAGE__PROVIDER_SECRETS_DIR` | next to DB under `secrets/providers` | Owner-only provider credentials managed by Settings; preserved by catalog reset and removed by factory reset. |
| `storage.backup_dir` | `MUZILLA_STORAGE__BACKUP_DIR` | unset | If set, `apply --backup` copies each file's original here before its first write. |
| `paths.create_directories` | `MUZILLA_PATHS__CREATE_DIRECTORIES` | `false` | Rename mode: flat filenames only (default) vs. creating subdirectories. |
| `retention.journal_days` / `retention.journal_changesets` | `MUZILLA_RETENTION__*` | `30` / `500` | See the retention window section below. |
| `metrics.enabled` | `MUZILLA_METRICS__ENABLED` | `false` | Exposes `GET /api/metrics` (Prometheus format, unauthenticated) — reveals library size, so opt-in, and never route it through a tunnel. |

Only `MUZILLA_AUTH__PASSWORD` and `MUZILLA_AUTH__SESSION_SECRET` are strictly required: in Docker every other key has a working default.

### Path templates — beets compatibility

Muzilla's filename/path template engine is intentionally beets-style compatible
(`$field`, `${field}`, `%func{a,b,...}` with nesting) and is verified by a
maintained upstream corpus, not by fuzz tests alone.

- **Corpus source (pinned, reproducible):** `beetbox/beets` tag `v2.10.0`
  (`5f6b2d35d186fb9f6402dfb7ec904b6c8383a6cd`, MIT) — raw file
  `https://raw.githubusercontent.com/beetbox/beets/v2.10.0/test/test_template.py`
  vendored **byte-identically** as `tests/fixtures/beets/test_template_v2.10.0.py.txt`
  (SHA-256 `4b65d8e6400b0a957c630f9f164b997ebb96082d9319813e326b211bf6b2a6d4`,
  see `tests/fixtures/beets/PROVENANCE.md`; license header preserved) and
  deterministically translated/mapped in `tests/paths/test_beets_compat.py`
  (no beets runtime dependency, no network in tests; each executed case is
  explicitly annotated with its upstream `ParseTest`/`EvalTest` identity and
  the executed suite proves the fixture SHA at runtime).
- **Supported subset:** literals/escapes, `$var`/`${var}`, function calls with
  1–N args and arbitrary nesting (`%upper`, `%lower`, `%title`, `%left`,
  `%right`, `%if`, `%ifdef`, `%asciify`, `%time`, `%first`, `%the` plus
  muzilla additions `%pad`, `%default`, `%sanitize`). Sanitization, beets-
  alias variables (`$albumartist` → `album_artist`, `$track` → `track_no`,
  …) and alias-aware `track_to_variables` are muzilla-owned and tested
  alongside.
- **Deliberate differences (explicit failure, never silent):**
  - Escapes are `$$` → `$` and `%%` → `%` only; beets also escapes `$%` → `%`,
    `$,` → `,`, `$}` → `}` — muzilla leaves `$%` literal or raises for stray
    `,`/`}` instead of silently rewriting.
  - Bare `%name` without `{`, unclosed `%name{…`, stray `}`/`,` outside a
    function, and unterminated/empty `${…}` all raise `TemplateError` with a
    precise offset; beets keeps them as literal text.
  - Empty function arg `%foo{}` → muzilla 0 args vs beets 1 empty arg.
  - Unknown function (including removed `%aunique`/`%sunique`) → muzilla
    raises `TemplateError: unknown function %…` at compile time; beets keeps
    the literal `%bar{}`. `%aunique`/`%sunique` are removed for product
    safety: muzilla never invents a unique name, collisions block Apply and
    are shown explicitly.
  - Wrong arity for a supported function (e.g. `%upper{a,b}`, `%left{a}`,
    `%if{a}`) → muzilla raises `TemplateError` at compile time via a
    shared arity boundary (`FUNCTION_ARITY` in the compiler); the raw
    Python `TypeError` never leaks. Beets’ E2E `test_function_call_exception`
    observes no exception for extra args, while muzilla fails explicitly.
  - Undefined `$var` → muzilla renders `""` (empty); beets keeps `"$bar"`.
  - `$,` inside function args: beets → single arg `"bar,baz"`; muzilla
    → two args `"bar$"`/`"baz"` — commas inside args must be supplied
    via variable values, not escaped in-template.
  - `%ifdef` first arg must be a bare field name (`%ifdef{title,…}`), not
    `$title`; beets behaves likewise but muzilla enforces it at render
    with an explicit `TemplateError`.
- **Fuzz:** `tests/paths/test_parser_fuzz.py` (Hypothesis) remains
  complementary assurance; it does not substitute for the corpus.

### Variables read by Compose, not by the app

Three of the variables in `.env.example` are interpolated by `docker-compose.yml` and never read by muzilla itself, so they do nothing in a bare-metal `muzilla serve` run:

| Variable | What reads it | Bare-metal equivalent |
| --- | --- | --- |
| `MUZILLA_LIBRARY_PATH` | Compose, as the `/music` bind-mount source | `MUZILLA_STORAGE__LIBRARY_ROOT` |
| `MUZILLA_BIND_ADDRESS` | Compose, as the published port's host interface | `muzilla serve --host` |
| `MUZILLA_PORT` | Compose, as the published port number | `muzilla serve --port` |

`MUZILLA_CONFIG_DIR` is the one application-read variable outside the `MUZILLA_<SECTION>__<KEY>` pattern: it names a directory whose `config.yaml` is loaded as a config layer.

## Undo and the retention window

Every apply is journaled before it writes, so the Undo button (and the current legacy
`muzilla changes undo <id>` support command) can request a revert — including a rename and
undo-of-undo (redo) — while the recovery state is retained and valid. Drift, collisions, or
uncertain recovery fail closed. The journal isn't kept forever: a background sweep prunes
journal rows once **either** 30 days have passed **or** the retained ChangeSets with journal
entries exceed 500, pruning those beyond the 500 most recent by earliest journal creation
time (whichever comes first — both configurable, see the table
above). Once a record's journal is pruned it is marked `undo_expired` and can no longer be
undone through the app — the tag/file changes themselves are untouched, only the ability to
revert them through Muzilla is gone. If you need a change reversible indefinitely, keep your
own backup (`storage.backup_dir` + `apply --backup`) rather than relying on the journal.

## Development

### Docker Pi sandbox

The repository includes a development image that runs the complete `pi` process inside Docker.
It is separate from the production image in `docker/Dockerfile` and includes Python 3.12,
Node 22, uv, Docker CLI, Playwright/Chromium, and the backend/frontend/E2E gate toolchains.
Docker Engine and the Compose plugin are required:

```bash
docker compose version
make sandbox-rebuild
make sandbox
```

`make sandbox` starts Pi with the repository at `/workspace`. The project `.pi/` directory,
`.env`, and sandbox definition files are read-only mounts; dependency trees, `.venv`, and
runtime data use disposable mounts. `README.md` and `docs/` remain writable so the agent can
update documentation. `music/` is the repository's disposable test library only: never mount
a user library into this sandbox.

The host Pi package manifest and lockfile are baked into the image and synchronized from
`~/.pi/agent` at each start. Project skills under `.agents/skills` and `.mcp.json` are already
available through the repository mount. The host Pi auth file is exposed only through the
read-only `/host-pi` mount; the Pi HOME, sessions, and sandbox-local memory persist in the
`muzilla-sandbox-pi-home` volume. A change to `.pi/settings.json` is visible on the next
sandbox start and does not require an image rebuild. Rebuild only after changing the image,
base-toolchain, or baked extension inputs:

```bash
make sandbox-rebuild
GITHUB_TOKEN=... make sandbox ARGS=--push
```

Pass credentials only for the session that needs them. `--push` fails unless
`GITHUB_TOKEN` or `GH_TOKEN` is present. Provider and model tokens use the explicit
`MUZILLA_*`, provider alias, and common Pi credential variables listed in
`docker-compose.sandbox.yml`; `.env` is never mounted.

The full gate environment is available inside Pi. `uv sync --frozen --all-extras` and locked
`npm ci` for `frontend/` and `e2e/` run at startup. The regression check also verifies read-only
mounts, all 11 configured system extensions, and builds the exact production Docker runtime
through the forwarded Docker socket:

```bash
make sandbox-test
```

Sandbox integrity (Q1-Q15): `make sandbox` auto-verifies the sandbox definition (`docker/sandbox-protected.list`) via `git diff` (fail-closed, bypass with `SANDBOX_ALLOW_DIRTY=1 make sandbox` or `make sandbox ARGS="--force"`), warns redacted on `.env` drift, auto-rebuilds the image when protected files or `e2e/package-lock.json` change (proxy allowlist, retention 5 for `pi-home` backups), and shows a yellow drift summary only when healed. Use `make sandbox-verify` for a dry-run check without starting Pi.

The forwarded Docker socket is required for image-level gates and is a deliberate
Docker-outside-of-Docker trade-off: a process that can use the socket can control the host
Docker daemon. The shell guards block common destructive commands, but they are not a kernel
security boundary. Use one sandbox per checkout; concurrent sessions on the same worktree can
race on `.git` and source files. For a manual browser-visible server, publish the loopback
port explicitly:

```bash
docker compose -f docker-compose.sandbox.yml run --rm --service-ports sandbox \
  sh -lc 'uv run muzilla serve --host 0.0.0.0 --port 8080'
```

Pi HOME export/import and cleanup are explicit and protected by confirmation:

```bash
make sandbox-logs
make sandbox-import
make sandbox-clean
```

Sandbox Docker resources use the `muzilla-sandbox-*` prefix, including the named volumes,
network, and image. A second project must use a different repository prefix, project name,
image, and host port; never use generic names such as `sandbox-pi-home` or a fixed
`container_name`. This permits multiple project sandboxes on one Docker host without sharing
Pi sessions or caches.

Backend:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,audio]"
muzilla serve --reload
```

`[audio]` is required, not optional, despite the name — it pulls in `pillow` (album art) and `pyacoustid` (fingerprinting), both on real code paths, not just the fingerprint-specific one. `.[dev]` alone will `ModuleNotFoundError` on `PIL` the first time an art-embedding path runs.

`muzilla serve` will not start on a fresh checkout until storage and auth environment variables are set: the packaged defaults point at the container's `/music` and `/data`, and auth is mandatory. See [Environment variables for a local run](CONTRIBUTING.md#environment-variables-for-a-local-run) for the exact block to export.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to `http://localhost:8080`, matching `muzilla serve`'s default port — unrelated to the Docker container's published port 1846.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development checks and
[`docs/production-readiness.md`](docs/production-readiness.md) for the complete candidate
gate.

## Stack

Python 3.12 / FastAPI / SQLite core, with a Typer CLI and a React GUI served from the same
container. Architecture and product boundaries are in
[`docs/product-spec.md`](docs/product-spec.md).

## License

MIT — see [LICENSE](LICENSE).
