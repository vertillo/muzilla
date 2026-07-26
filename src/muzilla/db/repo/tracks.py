"""Track catalog queries: browse, search, detail.

Cursor pagination throughout — offset pagination over a sorted 100k-row
table is a well-known trap (each page gets slower as OFFSET grows, and
results shift under concurrent writes).

The cursor encodes the last row's (sort_value, id) pair rather than just
its id: rows are ordered by the chosen sort column with id only as a
tiebreaker, so an id-only cursor would skip or repeat rows whenever the
sort order diverges from insertion order.
"""

from __future__ import annotations

import json
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, func, or_, select, text
from sqlalchemy.orm import Session

from muzilla.db.models import Track


@dataclass(frozen=True, slots=True)
class TrackPage:
    items: list[Track]
    next_cursor: str | None
    total: int


def encode_cursor(sort_value: Any, track_id: int) -> str:
    if isinstance(sort_value, datetime):
        sort_value = sort_value.isoformat()
    raw = json.dumps([sort_value, track_id], separators=(",", ":"))
    return urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[Any, int]:
    padded = cursor + "=" * (-len(cursor) % 4)
    sort_value, track_id = json.loads(urlsafe_b64decode(padded.encode()).decode())
    return sort_value, int(track_id)


_SORTABLE_COLUMNS = {
    "title": Track.title,
    "artist": Track.artist,
    "album": Track.album,
    "added": Track.first_seen_at,
}


def _base_query(*, q: str | None) -> Select[tuple[Track]]:
    stmt = select(Track).where(Track.missing_since.is_(None))
    if q:
        # FTS5 MATCH via a correlated subquery keeps this composable with
        # the other filters below, rather than needing a raw join.
        stmt = stmt.where(
            Track.id.in_(
                select(text("rowid")).select_from(text("tracks_fts")).where(
                    text("tracks_fts MATCH :q")
                )
            )
        ).params(q=q)
    return stmt


def list_tracks(
    session: Session,
    *,
    q: str | None = None,
    sort: str = "title",
    cursor: str | None = None,
    limit: int = 100,
) -> TrackPage:
    sort_key = sort if sort in _SORTABLE_COLUMNS else "title"
    sort_col = _SORTABLE_COLUMNS[sort_key]

    stmt = _base_query(q=q)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    if cursor is not None:
        last_value, last_id = decode_cursor(cursor)
        # Keyset predicate: strictly past the last row in (sort_col, id)
        # order. NULLs sort first in SQLite ASC, so a NULL cursor value
        # only needs the "same value, larger id" branch plus everything
        # non-NULL after it.
        if last_value is None:
            stmt = stmt.where(
                or_(sort_col.is_not(None), and_(sort_col.is_(None), Track.id > last_id))
            )
        else:
            stmt = stmt.where(
                or_(
                    sort_col > last_value,
                    and_(sort_col == last_value, Track.id > last_id),
                )
            )

    stmt = stmt.order_by(sort_col.asc(), Track.id.asc()).limit(limit + 1)

    rows = list(session.scalars(stmt))
    has_more = len(rows) > limit
    items = rows[:limit]

    next_cursor = None
    if has_more and items:
        last = items[-1]
        next_cursor = encode_cursor(getattr(last, sort_key_attr(sort_key)), last.id)

    return TrackPage(items=items, next_cursor=next_cursor, total=total)


def sort_key_attr(sort: str) -> str:
    """Maps a sort key to the Track attribute holding its value."""
    return {"added": "first_seen_at"}.get(sort, sort)


def get_track(session: Session, track_id: int) -> Track | None:
    return session.get(Track, track_id)


def get_track_by_path(session: Session, path: str) -> Track | None:
    return session.scalar(select(Track).where(Track.path == path))
