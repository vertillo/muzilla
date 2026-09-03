"""OPS-RETENTION-001 P1: retention expiry must be fail-closed; P2: settings effective vs draft."""
from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import ApplyRun, Operation, ReviewFileJournal, Track
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import put_revision, transition_bundle
from muzilla.services.reviews import OperationDraft

FIXTURE_MP3 = Path(__file__).parent.parent / "fixtures/audio/silence.mp3"


def _copy_fixture(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _track_from_file(session: Session, file_path: Path) -> int:
    from datetime import UTC, datetime

    from muzilla.changes.writer import _meta_to_field_dict
    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.tags.reader import read_track
    from muzilla.tags.writer import write_fields

    stat = file_path.stat()
    meta = read_track(file_path)
    now = datetime.now(UTC)
    track = Track(
        path=str(file_path),
        filename=file_path.name,
        ext=file_path.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        title=meta.title or "Old title",
        artist=meta.artist or "Old artist",
        tag_hash=compute_tag_hash(meta),
        first_seen_at=now,
        last_scanned_at=now,
    )
    full = _meta_to_field_dict(track)
    write_fields(file_path, full)
    stat = file_path.stat()
    meta = read_track(file_path)
    th = compute_tag_hash(meta)
    track.size_bytes = stat.st_size
    track.mtime_ns = stat.st_mtime_ns
    track.tag_hash = th
    session.add(track)
    session.flush()
    return track.id


def _make_applied_run_with_journals(
    session: Session,
    *,
    tmp_path: Path,
    library_root: Path,
    logical_key: str,
    journal_created_at: datetime | None = None,
    run_state: str = "applied",
    result_recovery: bool = False,
) -> tuple[int, int, list[int]]:
    """Creates a bundle with one or two tracks, an applied run and journals. Returns (bundle_id, apply_run_id, track_ids)."""
    # create two tracks for richer test
    f1 = library_root / f"{logical_key.replace(':', '_')}_1.mp3"
    f2 = library_root / f"{logical_key.replace(':', '_')}_2.mp3"
    _copy_fixture(FIXTURE_MP3, f1)
    _copy_fixture(FIXTURE_MP3, f2)
    t1 = _track_from_file(session, f1)
    t2 = _track_from_file(session, f2)

    from muzilla.db.models import Track as TModel

    items = []
    ops = []
    for tid in [t1, t2]:
        tr = session.get(TModel, tid)
        assert tr is not None
        items.append(
            {
                "source_type": "track",
                "source_id": tid,
                "path": tr.path,
                "size_bytes": tr.size_bytes,
                "mtime_ns": tr.mtime_ns,
                "tag_hash": tr.tag_hash,
                "filename": tr.filename,
            }
        )
        ops.append(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=tid,
                current_value=tr.title,
                proposed_value=f"New title {tid}",
            )
        )
    write = put_revision(
        session,
        logical_key=logical_key,
        title=f"Bundle {logical_key}",
        scope_type="track",
        scope_id=t1,
        source_snapshot={"items": items},
        operations=tuple(ops),
    )
    # accept ops
    for op in session.scalars(select(Operation).where(Operation.proposal_revision_id == write.revision_id)):
        op.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    session.flush()

    # create apply run directly as applied
    from muzilla.db.models import ApplyRun as AR
    from muzilla.db.models import OperationAttempt

    now = journal_created_at or datetime.now(UTC)
    # create run with result
    run = AR(
        review_bundle_id=write.bundle_id,
        proposal_revision_id=write.revision_id,
        idempotency_key=f"applied-{logical_key}",
        state=run_state,
        manifest={
            "version": 1,
            "files": [
                {"track_id": t1, "operation_ids": []},
                {"track_id": t2, "operation_ids": []},
            ],
        },
        result={
            "state": run_state,
            "atomicity": "review_bundle",
            "files": [],
            "recovery_required": result_recovery,
        }
        if run_state in ("applied", "partially_applied", "failed") else None,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()

    # operation attempts for each op as applied
    ops_in_db = list(session.scalars(select(Operation).where(Operation.proposal_revision_id == write.revision_id)))
    for op in ops_in_db:
        att = OperationAttempt(
            apply_run_id=run.id,
            operation_id=op.id,
            attempted_value=op.proposed_value,
            state="applied",
        )
        session.add(att)
    session.flush()

    # journals for each track
    for tid in [t1, t2]:
        tr = session.get(TModel, tid)
        assert tr is not None
        j = ReviewFileJournal(
            apply_run_id=run.id,
            track_id=tid,
            path=tr.path,
            phase="tags",
            state="done",
            before_hash="before",
            after_hash="after",
            before_blob={"title": tr.title},
            before_path=tr.path,
            after_path=tr.path,
            created_at=journal_created_at or now,
            updated_at=journal_created_at or now,
        )
        session.add(j)
    session.commit()
    return write.bundle_id, run.id, [t1, t2]


def test_undo_expired_by_age_fails_closed_and_shows_expiry_in_detail(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    lib = tmp_path / "lib_age"
    lib.mkdir()
    bundle_id, run_id, _tids = _make_applied_run_with_journals(
        db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:age-expire",
        journal_created_at=datetime.now(UTC) - timedelta(days=40),
    )
    # Verify before sweep, detail says expired? Actually with 40 days old and default 30, sweep should prune
    from muzilla.pipeline.retention import sweep_apply_journals

    # effective retention is 30 days, so 40 days old should be pruned
    journals_before = db_session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run_id)).all()
    assert len(journals_before) == 2
    pruned, _ = sweep_apply_journals(db_session, journal_days=30, journal_changesets=500)
    assert pruned == 2
    db_session.commit()

    # GET detail should now show undo_expired true
    resp = client.get(f"/api/reviews/{bundle_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["apply_runs"]) == 1
    run_out = data["apply_runs"][0]
    assert run_out["id"] == run_id
    assert run_out["undo_expired"] is True
    assert "expired" in (run_out["undo_expiry_reason"] or "").lower()
    assert "age" in (run_out["undo_expiry_reason"] or "").lower() or "threshold" in (run_out["undo_expiry_reason"] or "").lower()

    # Attempt undo via API should fail closed with 409 containing expired
    undo_resp = client.post(
        f"/api/reviews/{bundle_id}/undo",
        json={"apply_run_id": run_id},
        headers={"Origin": "http://testserver", "X-CSRF-Token": client.get("/api/auth/status").json()["csrf_token"], "Idempotency-Key": "undo-age-expire"},
    )
    # Our service-level check returns 409; the handler maps ReviewUndoError to 409
    assert undo_resp.status_code == 409
    assert "expired" in undo_resp.text.lower()
    assert "retention" in undo_resp.text.lower()

    # Also direct worker undo should fail closed (not succeed)
    from muzilla.changes.bundle_undo import apply_review_undo_run
    from muzilla.db.models import ReviewUndoRun

    # Create an undo run manually to test worker path (since API blocked, we test worker directly with a pending run)
    # Create a ReviewUndoRun that would have been enqueued before expiry, then pruned journals, then worker runs
    # Simulate: create undo run directly via DB
    now = datetime.now(UTC)
    undo_run = ReviewUndoRun(
        review_bundle_id=bundle_id,
        source_apply_run_id=run_id,
        idempotency_key="direct-worker-age",
        state="pending",
        manifest={"files": [{"track_id": _tids[0]}, {"track_id": _tids[1]}], "job_ids": []},
        created_at=now,
        updated_at=now,
    )
    db_session.add(undo_run)
    db_session.commit()
    result = apply_review_undo_run(db_session, undo_run.id, library_root=lib)
    assert result.state == "failed"
    db_session.refresh(undo_run)
    assert undo_run.state == "failed"
    assert "expired" in (undo_run.error or "").lower()
    # Should be not retryable/recovery_required false (fail closed)
    assert undo_run.result is not None
    assert undo_run.result.get("recovery_required") is False
    files = undo_run.result.get("files")
    assert isinstance(files, list)
    assert all(not f.get("retryable", True) for f in files if isinstance(f, dict))


def test_undo_expired_by_count_fails_closed_and_shows_expiry_in_detail(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    lib = tmp_path / "lib_count"
    lib.mkdir()
    # Create 3 applied runs with journals, staggered by creation time, keep only 1 most recent
    now = datetime.now(UTC)
    b1, r1, _ = _make_applied_run_with_journals(db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:count-1", journal_created_at=now - timedelta(hours=3))
    b2, _r2, _ = _make_applied_run_with_journals(db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:count-2", journal_created_at=now - timedelta(hours=2))
    b3, r3, _ = _make_applied_run_with_journals(db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:count-3", journal_created_at=now - timedelta(hours=1))

    from muzilla.pipeline.retention import sweep_apply_journals

    # keep only 1 most recent => r1 and r2 should be pruned, r3 kept
    pruned, _ = sweep_apply_journals(db_session, journal_days=3650, journal_changesets=1)
    # r1 and r2 each have 2 journals, so 4 pruned
    assert pruned == 4
    db_session.commit()

    # b1 detail should show expired
    resp1 = client.get(f"/api/reviews/{b1}")
    assert resp1.status_code == 200
    assert resp1.json()["apply_runs"][0]["undo_expired"] is True
    assert "expired" in (resp1.json()["apply_runs"][0]["undo_expiry_reason"] or "").lower()

    # b2 also expired
    resp2 = client.get(f"/api/reviews/{b2}")
    assert resp2.json()["apply_runs"][0]["undo_expired"] is True

    # b3 not expired
    resp3 = client.get(f"/api/reviews/{b3}")
    assert resp3.json()["apply_runs"][0]["undo_expired"] is False

    # Undo for b1 should fail closed
    undo_resp = client.post(
        f"/api/reviews/{b1}/undo",
        json={"apply_run_id": r1},
        headers={"Origin": "http://testserver", "X-CSRF-Token": client.get("/api/auth/status").json()["csrf_token"], "Idempotency-Key": "undo-count-expire"},
    )
    assert undo_resp.status_code == 409
    assert "expired" in undo_resp.text.lower()

    # Undo for b3 should succeed to enqueue (not expired) — we don't run worker, just check enqueue succeeds 202
    undo_resp3 = client.post(
        f"/api/reviews/{b3}/undo",
        json={"apply_run_id": r3},
        headers={"Origin": "http://testserver", "X-CSRF-Token": client.get("/api/auth/status").json()["csrf_token"], "Idempotency-Key": "undo-count-keep"},
    )
    assert undo_resp3.status_code == 202


def test_retention_preserves_recovery_required_journals(client: TestClient, db_session: Session, tmp_path: Path) -> None:
    lib = tmp_path / "lib_recovery"
    lib.mkdir()
    # Create run with recovery_required True, old date
    _, r, _ = _make_applied_run_with_journals(
        db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:recovery-preserve",
        journal_created_at=datetime.now(UTC) - timedelta(days=40),
        run_state="failed",
        result_recovery=True,
    )
    # But our helper sets state failed, need to set result recovery true manually after? Already does for failed case.
    # For recovery preservation, the sweep checks result.recovery_required == True, so set result accordingly
    run = db_session.get(ApplyRun, r)
    assert run is not None
    # Ensure run state is failed but result says recovery_required true; however sweep only protects if state pending/applying or recovery_required true regardless of state?
    # In retention code, it protects any run where result.recovery_required True, irrespective of state.
    # Also need run.state not applied? Our helper used run_state failed, but our expiry check only for applied/partially_applied, so recovery run not counted for undo expiry.
    # Instead create a applied run with recovery_required true? But spec says recovery_required journals are protected.
    # Let's create a failed run that is actually failed with recovery_required, and ensure its journals not pruned.
    # To test preservation, we need a run that would otherwise be pruned by age but is protected.
    # Use applied run but set result recovery_required true manually (simulating failed recovery? Actually applied run shouldn't have recovery_required, but we can set to test protection.)
    run.state = "applied"
    run.result = {"state": "applied", "atomicity": "review_bundle", "files": [], "recovery_required": True}
    db_session.commit()

    # Reload b,r with journals still old
    from muzilla.pipeline.retention import sweep_apply_journals

    journals_before = db_session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == r)).all()
    assert len(journals_before) == 2
    pruned, _ = sweep_apply_journals(db_session, journal_days=7, journal_changesets=500)
    # Should be 0 because recovery_required protected
    assert pruned == 0
    journals_after = db_session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == r)).all()
    assert len(journals_after) == 2

    # Also test pending/applying protected
    _, r2, _ = _make_applied_run_with_journals(
        db_session, tmp_path=tmp_path, library_root=lib, logical_key="track:pending-preserve",
        journal_created_at=datetime.now(UTC) - timedelta(days=40),
        run_state="applying",
    )
    pruned2, _ = sweep_apply_journals(db_session, journal_days=7, journal_changesets=500)
    assert pruned2 == 0
    assert len(db_session.scalars(select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == r2)).all()) == 2
