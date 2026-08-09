from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.db.models import Operation, ReviewBundle, Track
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.services.proposals import ProposalComposer
from muzilla.services.reviews import OperationDraft, finish_task_attempt, start_task_attempt


def _track() -> Track:
    now = datetime.now(UTC)
    return Track(
        path="/music/original.mp3",
        filename="original.mp3",
        ext=".mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Old title",
        artist="Old artist",
        first_seen_at=now,
        last_scanned_at=now,
    )


def _candidate() -> ReleaseCandidate:
    return ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Album",
        album_artist="Artist",
        tracks=(CandidateTrack(position=1, title="New title", artist="New artist"),),
    )


def test_composer_aggregates_sections_and_uses_proposed_tags_for_default_rename(
    db_session: Session,
) -> None:
    track = _track()
    db_session.add(track)
    db_session.flush()

    composer = ProposalComposer(db_session)
    detail = composer.compose_candidate_for_scope(
        scope_type="track", scope_id=track.id, candidate=_candidate()
    )
    detail = composer.add_operations(
        detail.id,
        (
            OperationDraft(
                kind="embed_art",
                field="art",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"blob_id": 1},
                provenance={
                    "section": "cover",
                    "provider": "coverartarchive",
                    "width": 600,
                    "height": 600,
                },
            ),
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value={"text": "line", "synced": False, "provider": "lrclib"},
                provenance={"section": "lyrics"},
            ),
            OperationDraft(
                kind="set_replay_gain",
                field="rg_track_gain",
                target_type="track",
                target_id=track.id,
                current_value=None,
                proposed_value=-4.2,
                provenance={"section": "audio"},
            ),
        ),
    )

    operations = detail.current_revision.operations
    assert {operation.kind for operation in operations} == {
        "set_tag",
        "move_file",
        "embed_art",
        "write_lyrics",
        "set_replay_gain",
    }
    move = next(operation for operation in operations if operation.kind == "move_file")
    assert move.proposed_value == "Old artist - New title.mp3"
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 1


def test_manual_edit_preserves_unedited_candidate_and_manual_operations(db_session: Session) -> None:
    track = _track()
    db_session.add(track)
    db_session.flush()
    composer = ProposalComposer(db_session)

    composer.compose_candidate_for_scope(scope_type="track", scope_id=track.id, candidate=_candidate())
    first_manual = composer.compose_manual_track_edit(
        track_id=track.id, field_values={"title": "My title"}
    )
    combined = composer.compose_manual_track_edit(
        track_id=track.id, field_values={"artist": "My artist"}
    )

    metadata = {
        operation.field: operation.proposed_value
        for operation in combined.current_revision.operations
        if operation.kind == "set_tag"
    }
    assert first_manual.id == combined.id
    assert metadata["title"] == "My title"
    assert metadata["artist"] == "My artist"
    assert metadata["album"] == "Album"
    move = next(operation for operation in combined.current_revision.operations if operation.kind == "move_file")
    assert move.proposed_value == "My artist - My title.mp3"


def test_partial_failure_and_retry_stay_on_the_same_bundle(db_session: Session) -> None:
    track = _track()
    db_session.add(track)
    db_session.flush()
    detail = ProposalComposer(db_session).compose_candidate_for_scope(
        scope_type="track", scope_id=track.id, candidate=_candidate()
    )

    failed = start_task_attempt(db_session, detail.id, kind="lyrics", item_key=f"track:{track.id}")
    finish_task_attempt(db_session, failed, state="transient_failure", error="provider unavailable")
    retry = start_task_attempt(db_session, detail.id, kind="lyrics", item_key=f"track:{track.id}")
    finish_task_attempt(db_session, retry, state="not_found")
    db_session.flush()

    reopened = ProposalComposer.open_bundle_for_scope(
        db_session, scope_type="track", scope_id=track.id
    )
    assert reopened is not None and reopened.id == detail.id
    assert retry.attempt_no == 2
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 1


@pytest.mark.parametrize(
    "section_operation",
    [
        OperationDraft(
            kind="embed_art",
            field="art",
            target_type="track",
            target_id=0,
            current_value=None,
            proposed_value={"blob_id": 1},
            provenance={"section": "cover", "provider": "coverartarchive"},
        ),
        OperationDraft(
            kind="write_lyrics",
            field="lyrics",
            target_type="track",
            target_id=0,
            current_value=None,
            proposed_value={"text": "line", "synced": False, "provider": "lrclib"},
            provenance={"section": "lyrics"},
        ),
        OperationDraft(
            kind="set_replay_gain",
            field="rg_track_gain",
            target_type="track",
            target_id=0,
            current_value=None,
            proposed_value=-4.2,
            provenance={"section": "audio", "analyzer": "rsgain"},
        ),
    ],
    ids=["cover", "lyrics", "replaygain"],
)
def test_section_completion_preserves_accepted_unchanged_operation(
    db_session: Session, section_operation: OperationDraft
) -> None:
    track = _track()
    db_session.add(track)
    db_session.flush()
    composer = ProposalComposer(db_session)
    initial = composer.compose_candidate_for_scope(
        scope_type="track", scope_id=track.id, candidate=_candidate()
    )
    accepted = next(
        operation
        for operation in initial.current_revision.operations
        if operation.kind == "set_tag" and operation.field == "title"
    )
    persisted = db_session.get(Operation, accepted.id)
    assert persisted is not None
    persisted.decision = "accepted"
    accepted_proposal = persisted.proposed_value

    completed = composer.add_operations(
        initial.id,
        (
            OperationDraft(
                kind=section_operation.kind,
                field=section_operation.field,
                target_type=section_operation.target_type,
                target_id=track.id,
                current_value=section_operation.current_value,
                proposed_value=section_operation.proposed_value,
                provenance=section_operation.provenance,
                validation=section_operation.validation,
            ),
        ),
    )

    unchanged = next(
        operation
        for operation in completed.current_revision.operations
        if operation.kind == "set_tag" and operation.field == "title"
    )
    assert unchanged.proposed_value == accepted_proposal
    assert unchanged.decision == "accepted"
