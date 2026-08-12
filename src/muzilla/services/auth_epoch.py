"""Session revocation on logout.

Deliberately separate from services/auth.py, which stays pure crypto
with no Session parameter anywhere — this is the one piece of auth that
needs the DB, so it gets its own module rather than threading a Session
through every function in auth.py for one call site.

schema_meta is a single-row table; get_or_create_row handles the case
where no row exists yet (true today — nothing has ever inserted one;
migrations/versions/0001_baseline.py only creates the table).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import SchemaMeta


def get_or_create_row(session: Session) -> SchemaMeta:
    row = session.query(SchemaMeta).first()
    if row is None:
        row = SchemaMeta()
        session.add(row)
        session.flush()
    return row


def read_auth_epoch(session: Session) -> int:
    return get_or_create_row(session).auth_epoch


def bump_auth_epoch(session: Session) -> int:
    row = get_or_create_row(session)
    row.auth_epoch += 1
    session.commit()
    return row.auth_epoch
