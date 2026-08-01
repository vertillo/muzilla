from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from muzilla.domain.reviews import BundleState
from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle


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
        reference.rsplit("/", 1)[-1] for reference in operation_items["discriminator"]["mapping"].values()
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
    ]
    assert schemas["ProviderStatusOut"]["properties"]["state"]["enum"] == [
        "disabled",
        "not_configured",
        "checking",
        "operational",
        "temporary_unavailable",
        "invalid_credentials",
    ]
