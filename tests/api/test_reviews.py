from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ApplyRun, Job, Operation, ReviewBundle, TaskAttempt, Track
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


def _cover_review(session: Session, *, suffix: str) -> tuple[int, Track]:
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
        scope_type="track", scope_id=track.id, candidate=candidate
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
    assert schemas["ChangeOut"]["properties"]["apply_state"]["enum"] == [
        "pending",
        "applied",
        "failed",
        "conflicted",
    ]
    assert schemas["ChangeSetSummaryOut"]["properties"]["state"]["enum"] == [
        "draft",
        "applying",
        "applied",
        "partially_applied",
        "failed",
        "discarded",
        "reverted",
        "undo_expired",
    ]
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
    review_id, track = _cover_review(db_session, suffix="upload-cover")
    music_file = tmp_path / "upload-cover.mp3"
    music_file.write_bytes(b"unchanged music bytes")
    track.path = str(music_file)
    db_session.commit()

    uploaded = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=_image_bytes(),
        headers={"Content-Type": "image/jpeg"},
    )

    assert uploaded.status_code == 201
    candidate = uploaded.json()
    assert candidate == {
        "id": candidate["id"],
        "provider": "upload",
        "mime": "image/jpeg",
        "size": candidate["size"],
        "width": 300,
        "height": 300,
        "thumbnail_url": (
            f"/api/reviews/{review_id}/cover/candidates/{candidate['id']}/thumbnail"
        ),
        "blob_id": candidate["blob_id"],
    }

    detail = client.get(f"/api/reviews/{review_id}").json()
    assert detail["cover_candidates"] == [candidate]
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


def test_cover_candidate_cannot_be_selected_from_another_review(
    client: TestClient, db_session: Session
) -> None:
    first_review_id, _ = _cover_review(db_session, suffix="first-cover")
    second_review_id, _ = _cover_review(db_session, suffix="second-cover")
    uploaded = client.post(
        f"/api/reviews/{first_review_id}/cover/candidates",
        content=_image_bytes(),
        headers={"Content-Type": "image/jpeg"},
    )
    assert uploaded.status_code == 201

    response = client.post(
        f"/api/reviews/{second_review_id}/cover",
        json={"action": "select", "asset_candidate_id": uploaded.json()["id"]},
    )

    assert response.status_code == 422
    hidden_thumbnail = client.get(
        f"/api/reviews/{second_review_id}/cover/candidates/"
        f"{uploaded.json()['id']}/thumbnail"
    )
    assert hidden_thumbnail.status_code == 404


def test_cover_upload_rejects_declared_or_actual_unsupported_mime(
    client: TestClient, db_session: Session
) -> None:
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

    assert wrong_declared.status_code == 415
    assert mismatched.status_code == 415


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

    assert undecodable.status_code == 422
    assert oversized.status_code == 413


def test_cover_upload_rejects_body_over_the_configured_byte_limit(
    client: TestClient, db_session: Session
) -> None:
    review_id, _ = _cover_review(db_session, suffix="oversized-body-cover")

    response = client.post(
        f"/api/reviews/{review_id}/cover/candidates",
        content=b"0" * (10 * 1024 * 1024 + 1),
        headers={"Content-Type": "image/jpeg"},
    )

    assert response.status_code == 413
