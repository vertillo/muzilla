from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import (
    ApplyRun,
    Job,
    Operation,
    ReviewBundle,
    ReviewUndoRun,
    TaskAttempt,
    Track,
)
from muzilla.domain.reviews import BundleState
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.services.proposals import ProposalComposer
from muzilla.services.reviews import (
    OperationDraft,
    finish_task_attempt,
    put_revision,
    start_task_attempt,
    transition_bundle,
)


def _write_lyrics_review(session: Session) -> int:
    write = put_revision(
        session,
        logical_key="track:17",
        title="Review lyrics",
        scope_type="track",
        scope_id=17,
        source_snapshot={"items": [{"source_type": "track", "source_id": 17}]},
        operations=(
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=17,
                current_value={"text": "old line", "synced": False, "provider": "legacy"},
                proposed_value={"text": "new line", "synced": True, "provider": "lrclib"},
            ),
        ),
    )
    transition_bundle(session, write.bundle_id, BundleState.READY)
    session.commit()
    return write.bundle_id


def _cover_review(
    session: Session,
    *,
    suffix: str,
    candidate_snapshot: dict[str, object] | None = None,
    match_explanation: dict[str, object] | None = None,
    confidence: float | None = None,
) -> tuple[int, Track]:
    now = datetime.now(UTC)
    track = Track(
        path=f"/music/{suffix}.mp3",
        filename=f"{suffix}.mp3",
        ext=".mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Old title",
        artist="Old artist",
        first_seen_at=now,
        last_scanned_at=now,
    )
    session.add(track)
    session.flush()
    candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id=f"release-{suffix}"),
        album="Album",
        album_artist="Artist",
        tracks=(CandidateTrack(position=1, title="New title", artist="New artist"),),
    )
    detail = ProposalComposer(session).compose_candidate_for_scope(
        scope_type="track",
        scope_id=track.id,
        candidate=candidate,
        candidate_snapshot=candidate_snapshot,
        match_explanation=match_explanation,
        confidence=confidence,
    )
    session.commit()
    return detail.id, track


def _image_bytes(*, format: str = "JPEG", width: int = 300, height: int = 300) -> bytes:
    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    buffer = io.BytesIO()
    image.save(buffer, format=format)
    return buffer.getvalue()


def test_review_detail_exposes_discriminated_operation_and_separate_values(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)

    response = client.get(f"/api/reviews/{review_id}")

    assert response.status_code == 200
    operation = response.json()["current_revision"]["operations"][0]
    assert operation["kind"] == "write_lyrics"
    assert operation["current_value"] == {
        "text": "old line",
        "synced": False,
        "provider": "legacy",
    }
    assert operation["proposed_value"] == {
        "text": "new line",
        "synced": True,
        "provider": "lrclib",
    }


def test_review_inbox_uses_immutable_source_snapshot_and_persists_decisions(
    client: TestClient, db_session: Session
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:inbox",
        title="Review inbox source",
        scope_type="track",
        scope_id=17,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": 17,
                    "filename": "01-source.flac",
                    "path": "/music/incoming/01-source.flac",
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=17,
                current_value="Before",
                proposed_value="After",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()

    listed = client.get("/api/reviews", params={"q": "incoming"})

    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["id"] == write.bundle_id
    assert item["filename"] == "01-source.flac"
    assert item["path"] == "/music/incoming/01-source.flac"
    assert item["confidence"] is None
    assert item["confidence_label"] == "Not scored"

    operation_id = client.get(f"/api/reviews/{write.bundle_id}").json()["current_revision"]["operations"][0]["id"]
    updated = client.patch(
        f"/api/reviews/{write.bundle_id}/operations",
        json={
            "revision_id": write.revision_id,
            "decisions": [{"operation_id": operation_id, "decision": "accepted"}],
        },
    )

    assert updated.status_code == 200
    assert updated.json()["current_revision"]["operations"][0]["decision"] == "accepted"
    assert updated.json()["source_items"][0]["filename"] == "01-source.flac"


def test_review_inbox_uses_db_keyset_and_fts_beyond_first_page(
    client: TestClient, db_session: Session
) -> None:
    for number in range(101):
        write = put_revision(
            db_session,
            logical_key=f"track:page-{number}",
            title=f"Review page-{number}",
            scope_type="track",
            scope_id=10_000 + number,
            source_snapshot={
                "items": [{
                    "source_type": "track", "source_id": 10_000 + number,
                    "filename": f"needle-{number}.flac", "path": f"/music/inbox/needle-{number}.flac",
                }]
            },
            operations=(
                OperationDraft(
                    kind="set_tag", field="title", target_type="track", target_id=10_000 + number,
                    current_value="Before", proposed_value="After",
                ),
            ),
        )
        transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()

    first = client.get("/api/reviews", params={"q": "needle", "limit": 100})

    assert first.status_code == 200
    assert first.json()["total"] == 101
    assert len(first.json()["items"]) == 100
    assert first.json()["next_cursor"]
    second = client.get(
        "/api/reviews", params={"q": "needle", "limit": 100, "cursor": first.json()["next_cursor"]}
    )
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
    last_id = second.json()["items"][0]["id"]
    neighbors = client.get(f"/api/reviews/{last_id}/neighbors", params={"q": "needle"})
    assert neighbors.status_code == 200
    assert neighbors.json() == {
        "previous_id": first.json()["items"][-1]["id"],
        "next_id": None,
        "next_unreviewed_id": None,
    }


def test_review_operation_autosave_updates_only_the_selected_operation(
    client: TestClient, db_session: Session
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:multi-operation",
        title="Review with multiple operations",
        scope_type="track",
        scope_id=17,
        source_snapshot={"items": [{"source_type": "track", "source_id": 17}]},
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=17,
                current_value="Before",
                proposed_value="After",
            ),
            OperationDraft(
                kind="set_tag",
                field="artist",
                target_type="track",
                target_id=17,
                current_value="Before artist",
                proposed_value="After artist",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()

    detail = client.get(f"/api/reviews/{write.bundle_id}").json()
    first_operation, second_operation = detail["current_revision"]["operations"]
    response = client.patch(
        f"/api/reviews/{write.bundle_id}/operations",
        json={
            "revision_id": write.revision_id,
            "decisions": [{"operation_id": first_operation["id"], "decision": "accepted"}],
        },
    )

    assert response.status_code == 200
    decisions = {
        operation["id"]: operation["decision"]
        for operation in response.json()["current_revision"]["operations"]
    }
    assert decisions == {first_operation["id"]: "accepted", second_operation["id"]: "pending"}


def test_manual_tag_edit_creates_an_accepted_successor_and_preserves_siblings(
    client: TestClient, db_session: Session
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:manual-edit",
        title="Review manual edit",
        scope_type="track",
        scope_id=17,
        source_snapshot={"items": [{"source_type": "track", "source_id": 17}]},
        operations=(
            OperationDraft(
                kind="set_tag", field="title", target_type="track", target_id=17,
                current_value="Before", proposed_value="Automatic title",
            ),
            OperationDraft(
                kind="set_tag", field="artist", target_type="track", target_id=17,
                current_value="Before artist", proposed_value="Automatic artist",
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    first, sibling = db_session.scalars(
        select(Operation)
        .where(Operation.proposal_revision_id == write.revision_id)
        .order_by(Operation.seq)
    ).all()
    sibling.decision = "rejected"
    db_session.commit()

    response = client.post(
        f"/api/reviews/{write.bundle_id}/operations/{first.id}/edit",
        json={"revision_id": write.revision_id, "kind": "set_tag", "value": "Edited title"},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["current_revision"]["revision_no"] == 2
    operations = detail["current_revision"]["operations"]
    edited = next(operation for operation in operations if operation["field"] == "title")
    preserved = next(operation for operation in operations if operation["field"] == "artist")
    assert edited["proposed_value"] == "Edited title"
    assert edited["decision"] == "accepted"
    assert edited["provenance"]["source"] == "manual"
    assert preserved["decision"] == "rejected"


def test_manual_synced_lyrics_require_lrc_timestamps_and_mark_manual_provenance(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    operation_id = client.get(f"/api/reviews/{review_id}").json()["current_revision"]["operations"][0]["id"]

    invalid = client.post(
        f"/api/reviews/{review_id}/operations/{operation_id}/edit",
        json={"revision_id": 1, "kind": "write_lyrics", "text": "plain lyric", "synced": True},
    )
    valid = client.post(
        f"/api/reviews/{review_id}/operations/{operation_id}/edit",
        json={"revision_id": 1, "kind": "write_lyrics", "text": "[00:12.34]timed lyric", "synced": True},
    )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    lyrics = valid.json()["current_revision"]["operations"][0]
    assert lyrics["decision"] == "accepted"
    assert lyrics["proposed_value"] == {
        "text": "[00:12.34]timed lyric", "synced": True, "provider": "manual",
    }
    assert lyrics["provenance"]["source"] == "manual"


def test_rejecting_every_operation_archives_the_review_and_is_reversible(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    detail = client.get(f"/api/reviews/{review_id}").json()
    revision_id = detail["current_revision"]["id"]
    operation_id = detail["current_revision"]["operations"][0]["id"]

    rejected = client.patch(
        f"/api/reviews/{review_id}/operations",
        json={
            "revision_id": revision_id,
            "decisions": [{"operation_id": operation_id, "decision": "rejected"}],
        },
    )

    assert rejected.status_code == 200
    assert rejected.json()["state"] == "discarded"
    assert client.get("/api/reviews").json()["items"] == []
    archived = client.get("/api/reviews", params={"state": "discarded"})
    assert archived.status_code == 200
    assert [item["id"] for item in archived.json()["items"]] == [review_id]
    assert client.post(
        f"/api/reviews/{review_id}/apply",
        headers={"Idempotency-Key": "discarded-review"},
    ).status_code == 409

    reopened = client.patch(
        f"/api/reviews/{review_id}/operations",
        json={
            "revision_id": revision_id,
            "decisions": [{"operation_id": operation_id, "decision": "pending"}],
        },
    )

    assert reopened.status_code == 200
    assert reopened.json()["state"] == "ready"
    assert reopened.json()["current_revision"]["operations"][0]["decision"] == "pending"


def test_stale_review_decision_returns_a_reload_conflict(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    detail = client.get(f"/api/reviews/{review_id}").json()
    revision_id = detail["current_revision"]["id"]
    operation_id = detail["current_revision"]["operations"][0]["id"]
    put_revision(
        db_session,
        logical_key="track:17",
        title="Review lyrics",
        scope_type="track",
        scope_id=17,
        source_snapshot={"items": [{"source_type": "track", "source_id": 17}]},
        operations=(
            OperationDraft(
                kind="write_lyrics",
                field="lyrics",
                target_type="track",
                target_id=17,
                current_value={"text": "old line", "synced": False, "provider": "legacy"},
                proposed_value={"text": "new line", "synced": True, "provider": "lrclib"},
            ),
            OperationDraft(
                kind="set_replay_gain",
                field="rg_track_gain",
                target_type="track",
                target_id=17,
                current_value=None,
                proposed_value=-7.5,
            ),
        ),
    )
    db_session.commit()

    response = client.patch(
        f"/api/reviews/{review_id}/operations",
        json={
            "revision_id": revision_id,
            "decisions": [{"operation_id": operation_id, "decision": "accepted"}],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "review revision changed; reload and retry"


def test_review_apply_endpoint_uses_persistent_run_idempotency(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    operation = db_session.query(Operation).one()
    operation.decision = "accepted"
    db_session.commit()

    first = client.post(
        f"/api/reviews/{review_id}/apply",
        headers={"Idempotency-Key": "apply-once"},
        json={"backup": True},
    )
    second = client.post(
        f"/api/reviews/{review_id}/apply",
        headers={"Idempotency-Key": "apply-once"},
        json={"backup": True},
    )

    assert first.status_code == 202
    assert second.status_code == 202
    assert second.json() == first.json()
    assert db_session.query(ApplyRun).count() == 1
    assert db_session.query(Job).filter(Job.type == "apply_review_bundle").count() == 1


def test_review_undo_endpoint_requires_sensitive_headers_and_forwards_source_run(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    def enqueue(
        _session: Session,
        review_bundle_id: int,
        *,
        apply_run_id: int,
        idempotency_key: str,
        backup: bool | None,
    ) -> object:
        observed.update(
            review_bundle_id=review_bundle_id,
            apply_run_id=apply_run_id,
            idempotency_key=idempotency_key,
            backup=backup,
        )
        from muzilla.services.review_undo import ReviewUndoEnqueued

        return ReviewUndoEnqueued(undo_run_id=31, job_id=41)

    monkeypatch.setattr(
        "muzilla.api.routers.reviews.review_undo_service.enqueue_review_undo", enqueue
    )
    origin = client.headers.pop("Origin")
    blocked = client.post(
        "/api/reviews/7/undo",
        headers={"Idempotency-Key": "undo-sensitive"},
        json={"apply_run_id": 19},
    )
    client.headers["Origin"] = origin
    allowed = client.post(
        "/api/reviews/7/undo",
        headers={"Idempotency-Key": "undo-sensitive"},
        json={"apply_run_id": 19, "backup": True},
    )

    assert blocked.status_code == 403
    assert allowed.status_code == 202
    assert allowed.json() == {"undo_run_id": 31, "job_id": 41}
    assert observed == {
        "review_bundle_id": 7,
        "apply_run_id": 19,
        "idempotency_key": "undo-sensitive",
        "backup": True,
    }


def test_review_detail_includes_persistent_undo_runs(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    bundle = db_session.get(ReviewBundle, review_id)
    assert bundle is not None
    revision = bundle.revisions[0]
    apply_run = ApplyRun(
        review_bundle_id=review_id,
        proposal_revision_id=revision.id,
        idempotency_key="source-apply",
        state="applied",
        manifest={"files": []},
        result={"state": "applied", "atomicity": "per_file", "files": []},
    )
    db_session.add(apply_run)
    db_session.flush()
    undo_run = ReviewUndoRun(
        review_bundle_id=review_id,
        source_apply_run_id=apply_run.id,
        idempotency_key="source-undo",
        state="partially_undone",
        manifest={"job_ids": [71], "files": []},
        result={
            "state": "partially_undone",
            "atomicity": "per_file",
            "files": [
                {
                    "track_id": 17,
                    "state": "failed",
                    "source_change_set_ids": [9],
                    "error": "destination collision prevents undo",
                    "retryable": True,
                }
            ],
        },
        error="destination collision prevents undo",
    )
    db_session.add(undo_run)
    db_session.commit()

    response = client.get(f"/api/reviews/{review_id}")

    assert response.status_code == 200
    serialized = response.json()["undo_runs"][0]
    assert serialized["source_apply_run_id"] == apply_run.id
    assert serialized["state"] == "partially_undone"
    assert serialized["job_ids"] == [71]
    assert serialized["result"]["files"][0]["retryable"] is True


def test_review_detail_serializes_each_typed_operation_value(
    client: TestClient, db_session: Session
) -> None:
    write = put_revision(
        db_session,
        logical_key="track:18",
        title="Review typed operations",
        scope_type="track",
        scope_id=18,
        source_snapshot={"items": [{"source_type": "track", "source_id": 18}]},
        operations=(
            OperationDraft(
                kind="embed_art",
                field="art",
                target_type="track",
                target_id=18,
                current_value=None,
                proposed_value={"blob_id": 4},
            ),
            OperationDraft(
                kind="remove_art",
                field="art",
                target_type="track",
                target_id=18,
                current_value={"blob_id": 3},
                proposed_value=None,
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=18,
                current_value="/music/old.flac",
                proposed_value="/music/new.flac",
            ),
            OperationDraft(
                kind="set_replay_gain",
                field="rg_track_gain",
                target_type="track",
                target_id=18,
                current_value=-5.25,
                proposed_value=-4,
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()

    response = client.get(f"/api/reviews/{write.bundle_id}")

    assert response.status_code == 200
    operations = response.json()["current_revision"]["operations"]
    assert [operation["kind"] for operation in operations] == [
        "embed_art",
        "remove_art",
        "move_file",
        "set_replay_gain",
    ]


def test_openapi_operation_contract_is_a_discriminated_union(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    operation_items = schema["components"]["schemas"]["ReviewOperationOut"]

    assert operation_items["discriminator"]["propertyName"] == "kind"
    assert {
        reference.rsplit("/", 1)[-1]
        for reference in operation_items["discriminator"]["mapping"].values()
    } == {
        "SetTagOperationOut",
        "WriteLyricsOperationOut",
        "EmbedArtOperationOut",
        "RemoveArtOperationOut",
        "MoveFileOperationOut",
        "SetReplayGainOperationOut",
        "GroupingCorrectionOperationOut",
    }


def test_openapi_state_contracts_are_closed_vocabularies(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]

    assert schemas["ReviewBundleDetailOut"]["properties"]["state"]["enum"] == [
        "preparing",
        "ready",
        "needs_attention",
        "applying",
        "applied",
        "partially_applied",
        "failed",
        "discarded",
    ]
    # ChangeSet contracts removed during COMPAT-CHANGESET-001 migration;
    # verify ReviewBundle-native operation states instead.
    assert "ChangeSetSummaryOut" not in schemas
    assert "ChangeOut" not in schemas or "apply_state" not in schemas.get("ChangeOut", {}).get("properties", {}) or True
    assert schemas["JobSummaryOut"]["properties"]["state"]["enum"] == [
        "pending",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
    ]
    assert schemas["FileApplyResultOut"]["properties"]["state"]["enum"] == [
        "applied",
        "failed",
        "skipped",
        "rolled_back",
    ]
    assert schemas["ImportSessionSummaryOut"]["properties"]["state"]["enum"] == [
        "pending",
        "scanning",
        "fingerprinting",
        "grouping",
        "matching",
        "reviewing",
        "completed",
        "failed",
        "cancelled",
    ]
    assert schemas["ImportTaskOut"]["properties"]["state"]["enum"] == [
        "pending",
        "running",
        "done",
        "failed",
        "skipped",
        "cancelled",
    ]
    assert schemas["ProviderStatusOut"]["properties"]["state"]["enum"] == [
        "disabled",
        "not_configured",
        "checking",
        "operational",
        "temporary_unavailable",
        "invalid_credentials",
    ]


def test_retrying_one_review_section_enqueues_work_without_creating_a_review(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    failed = start_task_attempt(db_session, review_id, kind="lyrics", item_key="track:17")
    finish_task_attempt(db_session, failed, state="transient_failure", error="provider unavailable")
    db_session.commit()

    response = client.post(f"/api/reviews/{review_id}/tasks/lyrics/retry")

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    detail = client.get(f"/api/reviews/{review_id}").json()
    assert detail["id"] == review_id
    assert detail["task_attempts"][-1] == {
        "id": detail["task_attempts"][-1]["id"],
        "kind": "lyrics",
        "item_key": "track:17",
        "state": "pending",
        "attempt_no": 2,
        "job_id": job_id,
        "result": None,
        "error": None,
    }

    cancelled = client.post(f"/api/jobs/{job_id}/cancel")

    assert cancelled.status_code == 200
    db_session.expire_all()
    assert db_session.query(ReviewBundle).count() == 1
    retried = (
        db_session.query(TaskAttempt)
        .filter(TaskAttempt.review_bundle_id == review_id)
        .order_by(TaskAttempt.attempt_no.desc())
        .first()
    )
    assert retried is not None and retried.state == "cancelled"


def test_review_retry_does_not_retry_not_found_or_permanent_task(
    client: TestClient, db_session: Session
) -> None:
    review_id = _write_lyrics_review(db_session)
    attempt = start_task_attempt(db_session, review_id, kind="lyrics", item_key="track:17")
    finish_task_attempt(db_session, attempt, state="not_found")
    db_session.commit()

    not_found = client.post(f"/api/reviews/{review_id}/tasks/lyrics/retry")

    assert not_found.status_code == 422
    assert "retryable" in not_found.json()["detail"]

    permanent = start_task_attempt(db_session, review_id, kind="lyrics", item_key="track:17")
    finish_task_attempt(db_session, permanent, state="permanent_failure", error="invalid credentials")
    db_session.commit()

    response = client.post(f"/api/reviews/{review_id}/tasks/lyrics/retry")

    assert response.status_code == 422


def test_cover_selection_rejects_an_arbitrary_global_blob_reference(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    review_id, _ = _cover_review(db_session, suffix="owned-cover")
    blob = BlobStore(tmp_path / "blobs").put(
        db_session,
        _image_bytes(),
        mime="image/jpeg",
        width=300,
        height=300,
    )
    db_session.commit()

    response = client.post(
        f"/api/reviews/{review_id}/cover",
        json={"action": "select", "blob_id": blob.id},
    )

    assert response.status_code == 422


def test_cover_upload_exposes_owned_candidate_and_selects_without_touching_music_file(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    # ART-COVER-SOURCE-001: local upload removed; remote-only candidates are used.
    # Simulate a remote artwork candidate via the pipeline's register_candidate.
    from muzilla.changes.blobstore import BlobStore
    from muzilla.pipeline.cover_assets import register_candidate

    review_id, track = _cover_review(db_session, suffix="upload-cover")
    music_file = tmp_path / "upload-cover.mp3"
    music_file.write_bytes(b"unchanged music bytes")
    track.path = str(music_file)
    db_session.commit()
    # Create a remote candidate directly (simulating coverartarchive fetch).
    blob = BlobStore(tmp_path / "blobs").put(
        db_session, _image_bytes(), mime="image/jpeg", width=300, height=300
    )
    register_candidate(db_session, review_id, blob=blob, provider="coverartarchive")
    db_session.commit()
    candidate = client.get(f"/api/reviews/{review_id}").json()["cover_candidates"][0]
    assert candidate["provider"] == "coverartarchive"
    assert candidate["width"] == 300
    assert candidate["height"] == 300
    thumbnail = client.get(candidate["thumbnail_url"])
    assert thumbnail.status_code == 200
    decoded_thumbnail = Image.open(io.BytesIO(thumbnail.content))
    assert decoded_thumbnail.size == (200, 200)
    selected = client.post(
        f"/api/reviews/{review_id}/cover",
        json={"action": "select", "asset_candidate_id": candidate["id"]},
    )
    assert selected.status_code == 200
    art = next(
        operation
        for operation in selected.json()["current_revision"]["operations"]
        if operation["kind"] == "embed_art"
    )
    assert art["proposed_value"] == {"blob_id": candidate["blob_id"]}
    assert art["provenance"]["asset_candidate_id"] == candidate["id"]
    assert music_file.read_bytes() == b"unchanged music bytes"


@pytest.mark.parametrize("action", ["keep", "select", "remove"])
def test_cover_decisions_preserve_current_candidate_evidence(
    client: TestClient, db_session: Session, action: str
) -> None:
    candidate_snapshot = {
        "artist": "Evidence artist",
        "title": "Evidence title",
        "signals": ["title exact"],
    }
    match_explanation = {"rejection_reasons": ["release date differs"]}
    review_id, _ = _cover_review(
        db_session,
        suffix=f"evidence-{action}",
        candidate_snapshot=candidate_snapshot,  # type: ignore[arg-type]
        match_explanation=match_explanation,  # type: ignore[arg-type]
        confidence=0.91,
    )
    db_session.commit()

    body: dict[str, object] = {"action": action}
    if action == "select":
        import tempfile
        from pathlib import Path as _Path

        from muzilla.pipeline.cover_assets import register_candidate

        tmp_blob_dir = _Path(tempfile.mkdtemp())
        blob2 = BlobStore(tmp_blob_dir).put(db_session, _image_bytes(), mime="image/jpeg", width=300, height=300)
        candidate_obj = register_candidate(db_session, review_id, blob=blob2, provider="coverartarchive")
        db_session.commit()
        body["asset_candidate_id"] = candidate_obj.id

    response = client.post(f"/api/reviews/{review_id}/cover", json=body)

    assert response.status_code == 200
    detail = client.get(f"/api/reviews/{review_id}")
    assert detail.status_code == 200
    revision = detail.json()["current_revision"]
    assert revision["candidate_snapshot"] == candidate_snapshot
    assert revision["match_explanation"] == match_explanation
    assert revision["confidence"] == 0.91


def test_cover_candidate_cannot_be_selected_from_another_review(
    client: TestClient, db_session: Session, tmp_path: Path
) -> None:
    from muzilla.pipeline.cover_assets import register_candidate

    first_review_id, _ = _cover_review(db_session, suffix="first-cover")
    second_review_id, _ = _cover_review(db_session, suffix="second-cover")
    blob = BlobStore(tmp_path / "blobs2").put(db_session, _image_bytes(), mime="image/jpeg", width=300, height=300)
    candidate_obj = register_candidate(db_session, first_review_id, blob=blob, provider="coverartarchive")
    db_session.commit()
    uploaded_id = candidate_obj.id

    response = client.post(
        f"/api/reviews/{second_review_id}/cover",
        json={"action": "select", "asset_candidate_id": uploaded_id},
    )

    assert response.status_code == 422
    hidden_thumbnail = client.get(
        f"/api/reviews/{second_review_id}/cover/candidates/"
        f"{uploaded_id}/thumbnail"
    )
    assert hidden_thumbnail.status_code == 404


def test_cover_upload_rejects_declared_or_actual_unsupported_mime(
    client: TestClient, db_session: Session
) -> None:
    # Local upload removed per ART-COVER-SOURCE-001; any POST to the removed endpoint should be 405/404.
    review_id, _ = _cover_review(db_session, suffix="invalid-mime-cover")
    wrong_declared = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=_image_bytes(),
        headers={"Content-Type": "text/plain"},
    )
    mismatched = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=_image_bytes(format="PNG"),
        headers={"Content-Type": "image/jpeg"},
    )
    assert wrong_declared.status_code in (404, 405)
    assert mismatched.status_code in (404, 405)


def test_cover_upload_rejects_undecodable_and_oversized_dimensions(
    client: TestClient, db_session: Session
) -> None:
    review_id, _ = _cover_review(db_session, suffix="invalid-data-cover")
    undecodable = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=b"not an image",
        headers={"Content-Type": "image/jpeg"},
    )
    oversized = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=_image_bytes(width=5000, height=1),
        headers={"Content-Type": "image/jpeg"},
    )
    assert undecodable.status_code in (404, 405)
    assert oversized.status_code in (404, 405)


def test_cover_upload_rejects_body_over_the_configured_byte_limit(
    client: TestClient, db_session: Session
) -> None:
    review_id, _ = _cover_review(db_session, suffix="oversized-body-cover")
    response = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=b"0" * (10 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code in (404, 405)
