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
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy.orm import Session

from muzilla.db.batching import batched
from muzilla.db.fts import encode_fts5_literal
from muzilla.db.models import Track


@dataclass(frozen=True, slots=True)
class TrackPage:
    items: list[Track]
    next_cursor: str | None
    total: int


@dataclass(frozen=True, slots=True)
class FacetValue:
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class TrackFacets:
    artists: list[FacetValue]
    albums: list[FacetValue]
    genres: list[FacetValue]
    formats: list[FacetValue]


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


_FLAG_PREDICATES = {
    "missing": lambda: Track.missing_since.is_not(None),
    "missing-art": lambda: Track.has_embedded_art.is_(False),
    "unmatched": lambda: Track.album.is_(None),
    "errored": lambda: Track.probe_error.is_not(None),
}


def _base_query(
    *,
    q: str | None,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: tuple[str, ...] = (),
) -> Select[tuple[Track]]:
    include_missing = "missing" in flags
    stmt = select(Track)
    if not include_missing:
        stmt = stmt.where(Track.missing_since.is_(None))
    fts_query = encode_fts5_literal(q)
    if fts_query is not None:
        # FTS5 MATCH via a correlated subquery keeps this composable with
        # the other filters below, rather than needing a raw join.
        stmt = stmt.where(
            Track.id.in_(
                select(text("rowid")).select_from(text("tracks_fts")).where(
                    text("tracks_fts MATCH :q")
                )
            )
        ).params(q=fts_query)
    if artist:
        stmt = stmt.where(Track.artist == artist)
    if album:
        stmt = stmt.where(Track.album == album)
    if format:
        stmt = stmt.where(Track.format == format)
    if genre:
        # genre is a JSON array column (JSONList) — json_each unpacks it
        # into rows, so an EXISTS-correlated subquery checks membership
        # without loading the array into Python. SQLite's JSON1 extension
        # is built in on the versions this project targets.
        stmt = stmt.where(
            select(text("1"))
            .select_from(text("json_each(tracks.genre)"))
            .where(text("json_each.value = :genre"))
            .params(genre=genre)
            .exists()
        )
    for flag in flags:
        predicate = _FLAG_PREDICATES.get(flag)
        if predicate is not None:
            stmt = stmt.where(predicate())
    return stmt


def list_tracks(
    session: Session,
    *,
    q: str | None = None,
    sort: str = "title",
    direction: str = "asc",
    cursor: str | None = None,
    limit: int = 100,
    artist: str | None = None,
    album: str | None = None,
    genre: str | None = None,
    format: str | None = None,
    flags: tuple[str, ...] = (),
) -> TrackPage:
    sort_key = sort if sort in _SORTABLE_COLUMNS else "title"
    sort_col = _SORTABLE_COLUMNS[sort_key]
    descending = direction == "desc"

    stmt = _base_query(q=q, artist=artist, album=album, genre=genre, format=format, flags=flags)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0

    if cursor is not None:
        last_value, last_id = decode_cursor(cursor)
        # Keyset predicate: strictly past the last row in (sort_col, id)
        # order. NULLs sort first in SQLite ASC, so a NULL cursor value
        # only needs the "same value, larger id" branch plus everything
        # non-NULL after it.
        if last_value is None and descending:
            stmt = stmt.where(and_(sort_col.is_(None), Track.id < last_id))
        elif last_value is None:
            stmt = stmt.where(
                or_(sort_col.is_not(None), and_(sort_col.is_(None), Track.id > last_id))
            )
        elif descending:
            stmt = stmt.where(
                or_(
                    sort_col.is_(None),
                    sort_col < last_value,
                    and_(sort_col == last_value, Track.id < last_id),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    sort_col > last_value,
                    and_(sort_col == last_value, Track.id > last_id),
                )
            )

    stmt = stmt.order_by(
        sort_col.desc() if descending else sort_col.asc(),
        Track.id.desc() if descending else Track.id.asc(),
    ).limit(limit + 1)

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


# Cap on distinct facet values returned per field. A flat 50k-track library
# realistically has a few thousand distinct artists at most; this bound
# exists so a pathological library (or a bug upstream) can't turn a facet
# request into an unbounded response. The UI truncates gracefully — a
# dropdown with 500 options is already unusable, so this is not a
# meaningfully lossy limit in practice.
_FACET_VALUE_LIMIT = 500


def get_facets(session: Session, *, q: str | None = None) -> TrackFacets:
    """Distinct artist/album/genre/format values (with counts), computed
    in SQL over the full table — not just whatever page(s) the client has
    fetched. Scoped to the current search string only, not to other active
    facet selections: see docs/PROGRESS.md for why ("narrow via search,
    always show all facet options" was chosen over "narrow as you go").
    """
    base = _base_query(q=q)

    def _scalar_facet(column: Any) -> list[FacetValue]:
        sub = base.subquery()
        col = sub.c[column.key]
        stmt = (
            select(col, func.count())
            .select_from(sub)
            .where(col.is_not(None))
            .group_by(col)
            .order_by(col.asc())
            .limit(_FACET_VALUE_LIMIT)
        )
        return [FacetValue(value=v, count=c) for v, c in session.execute(stmt)]

    artists = _scalar_facet(Track.artist)
    albums = _scalar_facet(Track.album)
    formats = _scalar_facet(Track.format)

    # genre is JSON-array-valued, so it needs json_each unpacked per row —
    # a plain GROUP BY on the column would group whole arrays, not values.
    # ids are fetched first (still one indexed, column-scoped query, not a
    # full-row ORM load) and then batched into the json_each query via
    # db/batching.py's helper, the same pattern used elsewhere in this
    # codebase for exactly this "large IN() over SQLite" shape (see
    # docs/PROGRESS.md gotcha #9/#22) — simpler and safer than threading a
    # raw-text FROM clause through the ORM-aware compiler, which does not
    # compose cleanly with an ORM-entity WHERE clause in one statement.
    filtered_ids = list(session.scalars(base.with_only_columns(Track.id)))
    genre_counts: dict[str, int] = {}
    for batch in batched(filtered_ids):
        rows = session.execute(
            text(
                "SELECT json_each.value, COUNT(*) FROM tracks, json_each(tracks.genre) "
                "WHERE tracks.id IN :ids GROUP BY json_each.value"
            ).bindparams(sa_bindparam("ids", expanding=True)),
            {"ids": batch},
        )
        for value, count in rows:
            genre_counts[value] = genre_counts.get(value, 0) + count
    genres = [
        FacetValue(value=v, count=genre_counts[v])
        for v in sorted(genre_counts)[:_FACET_VALUE_LIMIT]
    ]

    return TrackFacets(artists=artists, albums=albums, genres=genres, formats=formats)


def get_track(session: Session, track_id: int) -> Track | None:
    return session.get(Track, track_id)


def get_track_by_path(session: Session, path: str) -> Track | None:
    return session.scalar(select(Track).where(Track.path == path))
