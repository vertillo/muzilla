from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.db.models import Track, WorkUnit


def test_uncertain_track_exposes_constrained_grouping_review_without_mutating_it(
    client: TestClient, db_session: Session
) -> None:
    source = WorkUnit(
        key="api-source",
        kind="album",
        grouping_basis="tags",
        grouping_confidence=0.4,
        album="Shared album",
        album_artist="Shared artist",
        track_count=1,
    )
    compatible = WorkUnit(
        key="api-compatible",
        kind="album",
        grouping_basis="tags",
        grouping_confidence=1.0,
        album="shared ALBUM",
        album_artist="shared artist",
        track_count=1,
    )
    incompatible = WorkUnit(
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
        work_unit_id=source.id,
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
    # P1 regression: public grouping-correction must not leak internal work-unit IDs
    for operation in proposed:
        assert operation["kind"] == "grouping_correction"
        assert "group_id" not in operation["current_value"]
        assert "group_id" not in str(operation["current_value"])
        pv = operation["proposed_value"]
        assert "group_id" not in pv
        assert "source_group_id" not in pv
        assert "to_group_id" not in pv
        assert "singleton_key" not in pv
        # typed ID-free contract: only action
        assert set(pv.keys()) == {"action"}
        assert pv["action"] in {
            "confirm_collection",
            "treat_as_singleton",
            "move_to_collection",
        }
        # constrained correction preview must remain
        assert operation["validation"]["compatible"] is True
        preview = operation["validation"]["preview"]
        assert "current_collection" in preview
        assert "label" in preview
        assert "label" in operation["provenance"]
    move_ops = [op for op in proposed if op["proposed_value"]["action"] == "move_to_collection"]
    # Only the compatible collection (same normalized album/artist) is offered
    assert len(move_ops) == 1
    assert (
        "Shared album" in move_ops[0]["validation"]["preview"]["label"]
        or "shared ALBUM" in move_ops[0]["validation"]["preview"]["label"]
        or "compatibile" in move_ops[0]["provenance"]["label"].lower()
        or "Shared album" in move_ops[0]["provenance"]["label"]
    )
    # The incompatible collection must not be offered
    all_labels = " ".join(op["provenance"]["label"] for op in proposed)
    assert "Other album" not in all_labels

    db_session.refresh(track)
    db_session.refresh(source)
    db_session.refresh(compatible)
    assert track.work_unit_id == source.id
    assert source.is_pinned is False
    assert compatible.is_pinned is False
