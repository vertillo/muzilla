"""FastAPI dependencies: config and per-request DB sessions.

Handlers depend on `get_session`, never construct one directly, so the
session's lifetime always matches one request.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.services.db import session_scope


def get_config(request: Request) -> Config:
    return request.app.state.config  # type: ignore[no-any-return]


def get_session(request: Request) -> Iterator[Session]:
    config = get_config(request)
    with session_scope(config) as session:
        yield session
