"""FastAPI application factory.

Mounts /api routers first, then the built SPA last with a catch-all so
client-side routing survives a page refresh. See docs/product-spec.md
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from muzilla.api.deps import require_auth
from muzilla.api.middleware import (
    MutationGate,
    mutation_gate_middleware,
    security_headers_middleware,
)
from muzilla.api.routers import (
    auth,
    blobs,
    changesets,
    dashboard,
    duplicates,
    enrichment,
    fields,
    health,
    imports,
    jobs,
    matching,
    metrics,
    paths,
    providers,
    reviews,
    settings,
    tracks,
)
from muzilla.config.loader import load_config
from muzilla.logging import configure_logging
from muzilla.services import auth as auth_service
from muzilla.services import auth_epoch as auth_epoch_service
from muzilla.services import capabilities as capabilities_service
from muzilla.services import jobs as jobs_service
from muzilla.services import providers as providers_service
from muzilla.services import reset as reset_service
from muzilla.services import settings as settings_service
from muzilla.services.changesets import recover_apply_journal
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.secrets import FileSecretStore

_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_config()
    configure_logging(config.logging)
    # Fail fast rather than booting into a server that's either wide open
    # (auth disabled, MUZILLA_STORAGE__DB_PATH exposed) or permanently
    # locked out (auth enabled — the default — but no password/session
    # secret configured, so every request and even /api/auth/login would
    # 401/500 forever with nothing in the UI explaining why).
    if config.auth.enabled:
        resolved_password = config.auth.resolved_password()
        if resolved_password is None:
            raise RuntimeError(
                "MUZILLA_AUTH__ENABLED is true but no password is configured "
                "(set MUZILLA_AUTH__PASSWORD or MUZILLA_AUTH__PASSWORD_FILE), "
                "or set MUZILLA_AUTH__ENABLED=false to disable auth."
            )
        if config.auth.session_secret is None:
            raise RuntimeError(
                "MUZILLA_AUTH__ENABLED is true but MUZILLA_AUTH__SESSION_SECRET "
                "is not set."
            )
        # Hashed once here rather than per login attempt — see
        # services/auth.py::verify_password's docstring for why re-hashing
        # per attempt is a cheap unauthenticated DoS against the 2G
        # container.
        app.state.auth_password_hash = auth_service.hash_password(resolved_password)
    run_migrations(config)
    app.state.config = config
    app.state.csrf_secret = secrets.token_bytes(32)
    app.state.mutation_gate = MutationGate()
    app.state.runtime_capability_cache = capabilities_service.RuntimeCapabilityCache()
    provider_secret_store = FileSecretStore(config.storage.resolved_provider_secrets_dir())
    app.state.provider_secret_store = provider_secret_store

    # A crash after the persistent maintenance lock was acquired is recovered before
    # settings migration, provider clients, or workers can observe partial reset state.
    with session_scope(config) as reset_session:
        reset_service.recover_interrupted_reset(
            reset_session,
            config=config,
            secret_store=provider_secret_store,
        )

    # This data migration runs after Alembic (the settings table must exist)
    # and before clients/workers or requests can observe provider config.
    with session_scope(config) as settings_session:
        settings_service.migrate_legacy_provider_tokens(settings_session, provider_secret_store)

    # Startup crash recovery, before the worker pool starts: a job left
    # 'running' with an expired lease, or an apply_journal row left
    # mid-write, both mean a previous process died uncleanly. Neither
    # must be picked up as if everything were fine.
    with session_scope(config) as recovery_session:
        jobs_service.recover_stuck_jobs(recovery_session)
        recover_apply_journal(recovery_session, blob_dir=config.storage.blob_dir)
        recovery_session.commit()

    # Cached in app.state rather than read per request — require_auth
    # runs on every authenticated call, and a DB round-trip there is a
    # cost with no corresponding benefit in a single-writer,
    # single-process deployment. logout bumps both this and the DB value.
    with session_scope(config) as epoch_session:
        app.state.auth_epoch = auth_epoch_service.read_auth_epoch(epoch_session)

    resolver = providers_service.EffectiveConfigResolver(config, provider_secret_store)
    app.state.provider_config_resolver = resolver
    with session_scope(config) as settings_session:
        effective_provider_config = resolver.resolve(settings_session)
    provider_set = providers_service.build_provider_set(effective_provider_config)
    provider_runtime = providers_service.ProviderSetRuntime(provider_set, effective_provider_config)
    app.state.provider_runtime = provider_runtime
    app.state.provider_health_tasks = set()
    _schedule_provider_checks(app, provider_runtime)

    worker_controller = jobs_service.WorkerPoolController(
        config,
        provider_set,
        provider_runtime=provider_runtime,
    )
    app.state.worker_controller = worker_controller
    worker_task = await worker_controller.start()
    app.state.worker_task = worker_task

    try:
        yield
    finally:
        await worker_controller.shutdown()
        health_tasks = tuple(app.state.provider_health_tasks)
        for task in health_tasks:
            task.cancel()
        for task in health_tasks:
            with suppress(asyncio.CancelledError):
                await task
        await provider_runtime.close()


def _schedule_provider_checks(app: FastAPI, provider_runtime: providers_service.ProviderSetRuntime) -> None:
    """Run bounded probes without delaying readiness or holding a DB session."""
    task = asyncio.create_task(providers_service.check_all_provider_connections(provider_runtime))
    app.state.provider_health_tasks.add(task)
    task.add_done_callback(app.state.provider_health_tasks.discard)


def create_app() -> FastAPI:
    app = FastAPI(title="muzilla", lifespan=lifespan)
    app.middleware("http")(security_headers_middleware)
    app.middleware("http")(mutation_gate_middleware)

    app.include_router(health.router, prefix="/api")
    app.include_router(metrics.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(tracks.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(changesets.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(fields.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(matching.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(paths.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(jobs.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(imports.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(enrichment.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(duplicates.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(blobs.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(providers.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(dashboard.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(settings.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(reviews.router, prefix="/api", dependencies=[Depends(require_auth)])

    if _STATIC_DIR.is_dir():
        assets_dir = _STATIC_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

        index_file = _STATIC_DIR / "index.html"
        static_root = _STATIC_DIR.resolve()

        @app.get("/{full_path:path}", response_model=None)
        async def spa_catch_all(full_path: str) -> FileResponse | JSONResponse:
            # Client-side routing: any non-/api, non-/assets path serves the
            # SPA shell so refreshing a deep link like /dev/components works.
            #
            # This route is NOT behind require_auth (it's how a logged-out
            # client gets the SPA shell), so containment must hold entirely
            # on path resolution. pathlib's `/` discards the left operand
            # when the right side is absolute
            # (Path('/srv/static') / '/etc/passwd' -> PosixPath('/etc/passwd')),
            # so full_path must never reach a plain join unguarded.
            # .resolve() before the containment check is what handles `..`
            # and symlinks together — is_relative_to() on an unresolved path
            # does not.
            # Retired API routes must remain absent.  Letting the SPA fallback answer
            # `/api/groups` with index.html would make a removed endpoint look public
            # and conceal integration failures behind a 200 response.
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse({"detail": "not found"}, status_code=404)
            candidate = (static_root / full_path.lstrip("/")).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(static_root):
                return FileResponse(candidate)
            if index_file.is_file():
                # docs/product-spec.md: hashed assets (served via the /assets
                # StaticFiles mount above) get long Cache-Control;
                # index.html gets no-cache. Without this, a browser can
                # serve a cached shell referencing asset hashes that no
                # longer exist after an upgrade, and the app renders
                # blank with nothing the user can act on.
                return FileResponse(index_file, headers={"Cache-Control": "no-cache"})
            return JSONResponse({"detail": "not found"}, status_code=404)

    return app


app = create_app()
