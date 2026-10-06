"""SQLite transaction helpers for short read-then-write critical sections."""

from __future__ import annotations

from sqlalchemy.orm import Session


def begin_sqlite_write_transaction(session: Session) -> None:
    """Acquire SQLite's writer reservation before reading protected state.

    SQLite's default deferred transaction allows two callers to both observe
    missing work before either writes. Call this before the eligibility reads;
    the caller must commit/rollback promptly and must not hold the reservation
    across filesystem or other slow I/O.
    """
    connection = session.connection()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("this critical section requires SQLite writer serialization")
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("SQLite driver connection is unavailable")
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    session.flush()
