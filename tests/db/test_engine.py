from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from muzilla.db.engine import create_db_engine, create_session_factory


def test_wal_pragma_applied(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "test.db")
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode")).scalar()
        assert mode == "wal"
        fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert fk == 1


def test_session_factory_roundtrip(tmp_path: Path) -> None:
    from muzilla.db.models import Base, SchemaMeta

    engine = create_db_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    Session = create_session_factory(engine)

    with Session() as session:
        session.add(SchemaMeta())
        session.commit()

    with Session() as session:
        rows = session.query(SchemaMeta).all()
        assert len(rows) == 1
