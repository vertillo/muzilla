from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes import review_adapter
from muzilla.changes.review_adapter import (
    NoAcceptedReviewOperationsError,
    build_legacy_changeset_from_current_review,
)
from muzilla.db.models import Change, ChangeSet, Operation, Track
from muzilla.domain.reviews import BundleState
from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle


def _seed_track(session: Session) -> Track:
    track = Track(
        path="/music/review-adapter.mp3",
        filename="review-adapter.mp3",
        ext="mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Before",
        first_seen_at=datetime.now(UTC),
        last_scanned_at=datetime.now(UTC),
    )
    session.add(track)
    session.flush()
    return track


def _ready_review(session: Session, track: Track) -> int:
    write = put_revision(
        session,
        logical_key=f"track:{track.id}",
        title="Adapter review",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={"items": [{"source_type": "track", "source_id": track.id}]},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value="Before",
                proposed_value="After",
            ),
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"text": "line one", "synced": True, "provider": "lrclib"},
            ),
        ),
    )
    for operation in session.scalars(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    ):
        operation.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    session.flush()
    return write.bundle_id


def test_adapter_materializes_only_accepted_operations_as_legacy_changeset(
    db_session: Session,
) -> None:
    review_id = _ready_review(db_session, _seed_track(db_session))

    changeset_id = build_legacy_changeset_from_current_review(db_session, review_id)

    changeset = db_session.get(ChangeSet, changeset_id)
    assert changeset is not None
    assert changeset.source_ref["review_bundle_id"] == str(review_id)
    assert changeset.stats["accepted"] == 2
    changes = list(db_session.scalars(select(Change).where(Change.change_set_id == changeset_id)))
    lyrics = next(change for change in changes if change.op == "write_lyrics")
    assert lyrics.decision == "accepted"
    assert lyrics.new_value == {"text": "line one", "synced": True, "provider": "lrclib"}


def test_adapter_delegates_file_mutation_to_existing_applier(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    review_id = _ready_review(db_session, _seed_track(db_session))
    expected = object()
    called: dict[str, int] = {}

    def fake_apply(session: Session, changeset_id: int, **_kwargs: object) -> object:
        called["changeset_id"] = changeset_id
        return expected

    monkeypatch.setattr(review_adapter, "apply_changeset", fake_apply)

    result = review_adapter.apply_current_review_via_legacy_applier(db_session, review_id)

    assert result is expected
    assert db_session.get(ChangeSet, called["changeset_id"]) is not None


def test_adapter_does_not_materialize_or_apply_zero_accepted_operations(db_session: Session) -> None:
    track = _seed_track(db_session)
    write = put_revision(
        db_session,
        logical_key=f"track:{track.id}",
        title="Empty adapter review",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={"items": [{"source_type": "track", "source_id": track.id}]},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=track.id,
                current_value="Before",
                proposed_value="After",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)

    with pytest.raises(NoAcceptedReviewOperationsError):
        build_legacy_changeset_from_current_review(db_session, write.bundle_id)
