"""FastAPI application factory.

Mounts /api routers first, then the built SPA last with a catch-all so
client-side routing survives a page refresh. See docs/PLAN.md §9.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from muzilla.api.deps import require_auth
from muzilla.api.routers import (
    auth,
    changesets,
    enrichment,
    fields,
    groups,
    health,
    imports,
    jobs,
    matching,
    paths,
    tracks,
)
from muzilla.config.loader import load_config
from muzilla.services import jobs as jobs_service
from muzilla.services.changesets import recover_apply_journal
from muzilla.services.db import session_scope
from muzilla.services.migrate import run_migrations
from muzilla.services.providers import build_provider_set

_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config = load_config()
    # Fail fast rather than booting into a server that's either wide open
    # (auth disabled, MUZILLA_STORAGE__DB_PATH exposed) or permanently
    # locked out (auth enabled — the default — but no password/session
    # secret configured, so every request and even /api/auth/login would
    # 401/500 forever with nothing in the UI explaining why).
    if config.auth.enabled:
        if config.auth.resolved_password() is None:
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
    run_migrations(config)
    app.state.config = config

    # Startup crash recovery, before the worker pool starts: a job left
    # 'running' with an expired lease, or an apply_journal row left
    # mid-write, both mean a previous process died uncleanly. Neither
    # must be picked up as if everything were fine.
    with session_scope(config) as recovery_session:
        jobs_service.recover_stuck_jobs(recovery_session)
        recover_apply_journal(recovery_session)
        recovery_session.commit()

    provider_set = build_provider_set(config)
    app.state.provider_set = provider_set

    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(
        jobs_service.run_worker_pool(config, provider_set, stop_event)
    )
    app.state.worker_task = worker_task

    try:
        yield
    finally:
        stop_event.set()
        await worker_task
        for client in provider_set.clients:
            await client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="muzilla", lifespan=lifespan)

    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(tracks.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(changesets.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(groups.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(fields.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(matching.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(paths.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(jobs.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(imports.router, prefix="/api", dependencies=[Depends(require_auth)])
    app.include_router(enrichment.router, prefix="/api", dependencies=[Depends(require_auth)])

    if _STATIC_DIR.is_dir():
        assets_dir = _STATIC_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

        index_file = _STATIC_DIR / "index.html"

        @app.get("/{full_path:path}", response_model=None)
        async def spa_catch_all(full_path: str) -> FileResponse | JSONResponse:
            # Client-side routing: any non-/api, non-/assets path serves the
            # SPA shell so refreshing a deep link like /dev/components works.
            candidate = _STATIC_DIR / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            if index_file.is_file():
                return FileResponse(index_file)
            return JSONResponse({"detail": "not found"}, status_code=404)

    return app


app = create_app()
