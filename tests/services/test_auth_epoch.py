from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.services import auth_epoch


def test_read_auth_epoch_creates_row_when_none_exists(db_session: Session) -> None:
    # migrations/versions/0001_baseline.py never inserts a schema_meta
    # row itself — nothing did, until this feature needed one to exist.
    assert auth_epoch.read_auth_epoch(db_session) == 0


def test_bump_auth_epoch_increments_and_persists(db_session: Session) -> None:
    assert auth_epoch.bump_auth_epoch(db_session) == 1
    assert auth_epoch.bump_auth_epoch(db_session) == 2
    assert auth_epoch.read_auth_epoch(db_session) == 2


def test_epoch_persists_across_a_fresh_session(migrated_db: Path) -> None:
    from muzilla.db.engine import create_db_engine, create_session_factory

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)

    with factory() as s1:
        auth_epoch.bump_auth_epoch(s1)

    with factory() as s2:
        assert auth_epoch.read_auth_epoch(s2) == 1
