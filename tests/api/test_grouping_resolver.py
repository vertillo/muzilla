from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup


def test_uncertain_track_exposes_constrained_grouping_review_without_mutating_it(
    client: TestClient, db_session: Session
) -> None:
    source = TrackGroup(
        key="api-source",
        kind="album",
        grouping_basis="tags",
        grouping_confidence=0.4,
        album="Shared album",
        album_artist="Shared artist",
        track_count=1,
    )
    compatible = TrackGroup(
        key="api-compatible",
        kind="album",
        grouping_basis="tags",
        grouping_confidence=1.0,
        album="shared ALBUM",
        album_artist="shared artist",
        track_count=1,
    )
    incompatible = TrackGroup(
        key="api-incompatible",
        kind="album",
        grouping_basis="tags",
        grouping_confidence=1.0,
        album="Other album",
        album_artist="Other artist",
        track_count=1,
    )
    db_session.add_all([source, compatible, incompatible])
    db_session.flush()
    now = datetime.now(UTC)
    track = Track(
        path="/library/api-uncertain.mp3",
        filename="api-uncertain.mp3",
        ext="mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Track",
        artist="Shared artist",
        album="Shared album",
        album_artist="Shared artist",
        group_id=source.id,
        first_seen_at=now,
        last_scanned_at=now,
    )
    db_session.add(track)
    db_session.commit()

    response = client.post(f"/api/tracks/{track.id}/review/grouping")

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "needs_attention"
    proposed = body["current_revision"]["operations"]
    move_targets = {
        operation["proposed_value"]["to_group_id"]
        for operation in proposed
        if operation["proposed_value"]["action"] == "move_to_collection"
    }
    assert move_targets == {compatible.id}
    assert incompatible.id not in move_targets

    db_session.refresh(track)
    db_session.refresh(source)
    db_session.refresh(compatible)
    assert track.group_id == source.id
    assert source.is_pinned is False
    assert compatible.is_pinned is False
