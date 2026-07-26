"""Session wiring for API/CLI callers.

`api` and `cli` may not import `muzilla.db` (see the import-linter
contracts in pyproject.toml), so this is the one place that turns a
`Config` into a usable `Session` for the rest of the services layer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import Config
from muzilla.db.engine import create_db_engine, create_session_factory

_factories: dict[str, sessionmaker[Session]] = {}


def get_session_factory(config: Config) -> sessionmaker[Session]:
    """Cached per db_path so repeated calls share one engine/pool."""
    db_path = str(config.storage.db_path)
    factory = _factories.get(db_path)
    if factory is None:
        engine = create_db_engine(config.storage.db_path)
        factory = create_session_factory(engine)
        _factories[db_path] = factory
    return factory


@contextmanager
def session_scope(config: Config) -> Iterator[Session]:
    factory = get_session_factory(config)
    with factory() as session:
        yield session
