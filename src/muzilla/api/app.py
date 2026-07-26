"""FastAPI application factory.

Mounts /api routers first, then the built SPA last with a catch-all so
client-side routing survives a page refresh. See docs/PLAN.md §9.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from muzilla.api.routers import health
from muzilla.config.loader import load_config

_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.config = load_config()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="muzilla", lifespan=lifespan)

    app.include_router(health.router, prefix="/api")

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
