from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from muzilla.db.models import Track
from muzilla.services.catalog import browse_tracks, get_track_detail


def _make_track(**overrides: object) -> Track:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "path": "/music/track.mp3",
        "filename": "track.mp3",
        "ext": "mp3",
        "size_bytes": 1000,
        "mtime_ns": 1,
        "first_seen_at": now,
        "last_scanned_at": now,
    }
    defaults.update(overrides)
    return Track(**defaults)  # type: ignore[arg-type]


def test_browse_tracks_returns_summaries(db_session: Session) -> None:
    db_session.add(_make_track(title="Svefn-g-englar", artist="Sigur Rós"))
    db_session.commit()

    page = browse_tracks(db_session)

    assert page.total == 1
    assert len(page.items) == 1
    item = page.items[0]
    assert item.title == "Svefn-g-englar"
    assert item.artist == "Sigur Rós"


def test_get_track_detail_returns_full_fields(db_session: Session) -> None:
    track = _make_track(title="Starálfur", isrc="ISABC1234567")
    db_session.add(track)
    db_session.commit()

    detail = get_track_detail(db_session, track.id)

    assert detail is not None
    assert detail.title == "Starálfur"
    assert detail.isrc == "ISABC1234567"


def test_get_track_detail_missing_returns_none(db_session: Session) -> None:
    assert get_track_detail(db_session, 999) is None
