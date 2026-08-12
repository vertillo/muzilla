from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from muzilla.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

_MANAGED_FTS5_TABLES = frozenset({"review_inbox_entries_fts", "tracks_fts"})
# Both managed indexes use FTS5 external-content tables. SQLite therefore creates
# only config/data/docsize/idx shadows; ``*_content`` remains an ordinary name
# that Alembic must continue to report when it drifts.
_SQLITE_FTS5_SHADOW_SUFFIXES = frozenset({"config", "data", "docsize", "idx"})
_MANAGED_FTS5_OBJECTS = _MANAGED_FTS5_TABLES | frozenset(
    f"{table_name}_{suffix}"
    for table_name in _MANAGED_FTS5_TABLES
    for suffix in _SQLITE_FTS5_SHADOW_SUFFIXES
)


def _is_managed_fts5_table(name: str | None) -> bool:
    return name in _MANAGED_FTS5_OBJECTS


def _include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Exclude only migration-owned FTS5 tables and their SQLite shadow tables."""
    del object_, compare_to
    return not (type_ == "table" and reflected and _is_managed_fts5_table(name))


def _db_url() -> str:
    override = os.environ.get("MUZILLA_ALEMBIC_DB_PATH")
    if override:
        return f"sqlite:///{override}"
    return config.get_main_option("sqlalchemy.url") or "sqlite:///./data/muzilla.db"


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _db_url()
    connectable = engine_from_config(
        configuration, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
