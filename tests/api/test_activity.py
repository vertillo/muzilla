from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.jobs import queue
from muzilla.pipeline.reviews import OperationDraft, put_revision


def _enqueue(client_db: Path, job_type: str, payload: dict[str, object] | None = None) -> int:
    engine = create_db_engine(client_db)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.enqueue(session, type=job_type, payload=payload or {})
        return job.id


def test_activity_groups_by_user_action_and_hides_system(
    client: TestClient, migrated_db: Path
) -> None:
    # create scan (user scan), duplicate analysis, retention (system)
    _enqueue(migrated_db, "scan", {"root": "/music"})
    _enqueue(migrated_db, "detect_duplicates", {})
    _enqueue(migrated_db, "retention_sweep", {})

    # create import session via direct DB (start_import needs session)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.services.imports import start_import

        start_import(session, "/music/library")

    # create a review + apply run
    with factory() as session:
        rev = put_revision(
            session,
            logical_key="track:1",
            title="Test Bundle",
            scope_type="track",
            scope_id=1,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": 1,
                        "path": "/music/a.mp3",
                        "size_bytes": 1,
                        "mtime_ns": 1,
                        "tag_hash": "abc",
                    }
                ]
            },
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=1,
                    current_value="old",
                    proposed_value="new",
                ),
            ),
        )
        # need a track for bundle? bundle creation doesn't require track existence, but apply preflight will fail without track file
        # For activity we just need the row; state will be preparing but we transition to pending apply via start_apply_run
        from muzilla.pipeline.reviews import start_apply_run

        try:
            run = start_apply_run(session, rev.bundle_id, idempotency_key="test-apply-1")
            # enqueue job for it via service helper to link job

            # enqueue_review_apply would check preflight and fail due to missing track; so just manually create Job and link
            job = queue.enqueue(
                session, type="apply_review_bundle", payload={"apply_run_id": run.id}
            )
            run.manifest = {**run.manifest, "job_ids": [job.id]}
            session.commit()
        except Exception:
            session.rollback()
            # still have bundle but no apply run; activity will show one less apply - still test grouping
            pass
        session.commit()

    resp = client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.json()
    kinds = [i["kind"] for i in data["items"]]
    # should contain import, scan, duplicate_analysis, and potentially apply
    assert "import" in kinds
    assert "scan" in kinds
    assert "duplicate_analysis" in kinds
    # system job should not appear
    titles = [i["title"] for i in data["items"]]
    assert not any("Pulizia cronologia" in t for t in titles)
    # each item should have human title, state, outcome, and diagnostics id
    for item in data["items"]:
        assert item["title"]
        assert item["state"] in (
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "cancelling",
        )
        assert "id" in item and ":" in item["id"]
        # primary row must not be raw technical job type alone - title is human
        assert item["kind"] in ("import", "scan", "apply", "undo", "duplicate_analysis")

    # include_system true should surface retention sweep in diagnostics list
    resp2 = client.get("/api/activity?include_system=true")
    assert resp2.status_code == 200
    titles2 = [i["title"] for i in resp2.json()["items"]]
    assert any("Pulizia cronologia" in t for t in titles2)


def test_activity_shows_progress_and_cancellable_for_running_scan(
    client: TestClient, migrated_db: Path
) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        job = queue.enqueue(session, type="scan", payload={"root": "/music"})
        # lease it to running
        from datetime import UTC, datetime, timedelta

        job.state = "running"
        job.lease_until = datetime.now(UTC) + timedelta(seconds=60)
        job.progress_current = 2
        job.progress_total = 10
        job.progress_message = "scanning"
        session.commit()
        job_id = job.id

    resp = client.get("/api/activity")
    assert resp.status_code == 200
    item = next((i for i in resp.json()["items"] if i["job_id"] == job_id), None)
    assert item is not None
    assert item["state"] == "running"
    assert item["cancellable"] is True
    assert item["progress_current"] == 2
    assert item["progress_total"] == 10


def test_activity_pagination_cursor(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        for i in range(3):
            queue.enqueue(session, type="scan", payload={"root": f"/music/{i}"})
    resp = client.get("/api/activity?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2
    cursor = resp.json()["next_cursor"]
    assert cursor is not None
    # cursor must be keyset (base64), not plain offset integer
    assert not cursor.isdigit()
    resp2 = client.get(f"/api/activity?limit=2&cursor={cursor}")
    assert resp2.status_code == 200
    assert len(resp2.json()["items"]) >= 1
    ids1 = {i["id"] for i in resp.json()["items"]}
    ids2 = {i["id"] for i in resp2.json()["items"]}
    assert ids1.isdisjoint(ids2)


def test_activity_apply_and_undo_visible(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        rev = put_revision(
            session,
            logical_key="track:42",
            title="Bundle For Activity",
            scope_type="track",
            scope_id=42,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": 42,
                        "path": "/music/b.mp3",
                        "size_bytes": 1,
                        "mtime_ns": 1,
                        "tag_hash": "abc",
                    }
                ]
            },
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=42,
                    current_value="old",
                    proposed_value="new",
                ),
            ),
        )
        from sqlalchemy import select

        from muzilla.db.models import Operation
        from muzilla.domain.reviews import BundleState
        from muzilla.pipeline.reviews import start_apply_run, transition_bundle

        op = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == rev.revision_id)
        )
        assert op is not None
        op.decision = "accepted"
        transition_bundle(session, rev.bundle_id, BundleState.READY)
        run = start_apply_run(session, rev.bundle_id, idempotency_key="test-apply-visible")
        job = queue.enqueue(session, type="apply_review_bundle", payload={"apply_run_id": run.id})
        run.manifest = {**run.manifest, "job_ids": [job.id]}
        # create an undo run directly (avoid journal checks)
        from muzilla.db.models import ReviewUndoRun

        undo = ReviewUndoRun(
            review_bundle_id=rev.bundle_id,
            source_apply_run_id=run.id,
            idempotency_key="test-undo-visible",
            state="pending",
            manifest={"job_ids": [], "files": []},
        )
        session.add(undo)
        session.flush()
        undo_job = queue.enqueue(
            session, type="undo_review_bundle", payload={"undo_run_id": undo.id}
        )
        undo.manifest = {**undo.manifest, "job_ids": [undo_job.id]}
        session.commit()
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    kinds = [i["kind"] for i in resp.json()["items"]]
    assert "apply" in kinds
    assert "undo" in kinds
    apply_item = next(i for i in resp.json()["items"] if i["kind"] == "apply")
    assert "Applicazione" in apply_item["title"]
    assert apply_item["review_bundle_id"] is not None
    undo_item = next(i for i in resp.json()["items"] if i["kind"] == "undo")
    assert "Annullamento" in undo_item["title"]
    assert undo_item["review_bundle_id"] is not None


def test_activity_import_cancelling_prioritized(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.db.models import Job
        from muzilla.services.imports import start_import

        summary = start_import(session, "/music/cancel-import")
        # mark job as cancelling
        job = session.get(Job, summary.job_id)
        assert job is not None
        job.state = "cancelling"
        job.cancel_requested = True
        job.progress_current = 5
        job.progress_total = 10
        job.progress_message = "cancelling scan"
        session.commit()
        import_session_id = summary.id
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    item = next(
        (i for i in resp.json()["items"] if i["import_session_id"] == import_session_id), None
    )
    assert item is not None
    assert item["state"] == "cancelling"
    assert item["cancellable"] is False
    # progress should be visible even while cancelling
    assert item["progress_current"] == 5
    assert item["progress_total"] == 10


def test_activity_apply_cancelling_prioritized(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        rev = put_revision(
            session,
            logical_key="track:43",
            title="Bundle Cancel Apply",
            scope_type="track",
            scope_id=43,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": 43,
                        "path": "/music/c.mp3",
                        "size_bytes": 1,
                        "mtime_ns": 1,
                        "tag_hash": "abc",
                    }
                ]
            },
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=43,
                    current_value="old",
                    proposed_value="new",
                ),
            ),
        )
        from sqlalchemy import select

        from muzilla.db.models import Operation
        from muzilla.domain.reviews import BundleState
        from muzilla.pipeline.reviews import start_apply_run, transition_bundle

        op = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == rev.revision_id)
        )
        assert op is not None
        op.decision = "accepted"
        transition_bundle(session, rev.bundle_id, BundleState.READY)
        run = start_apply_run(session, rev.bundle_id, idempotency_key="test-apply-cancel")
        job = queue.enqueue(session, type="apply_review_bundle", payload={"apply_run_id": run.id})
        run.manifest = {**run.manifest, "job_ids": [job.id]}
        from muzilla.db.models import Job

        j = session.get(Job, job.id)
        assert j is not None
        j.state = "cancelling"
        j.cancel_requested = True
        session.commit()
        run_id = run.id
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    item = next((i for i in resp.json()["items"] if i["apply_run_id"] == run_id), None)
    assert item is not None
    assert item["state"] == "cancelling"
    assert item["cancellable"] is False


def test_activity_undo_cancelling_prioritized(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        rev = put_revision(
            session,
            logical_key="track:44",
            title="Bundle Cancel Undo",
            scope_type="track",
            scope_id=44,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": 44,
                        "path": "/music/d.mp3",
                        "size_bytes": 1,
                        "mtime_ns": 1,
                        "tag_hash": "abc",
                    }
                ]
            },
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=44,
                    current_value="old",
                    proposed_value="new",
                ),
            ),
        )
        from sqlalchemy import select

        from muzilla.db.models import Operation
        from muzilla.domain.reviews import BundleState
        from muzilla.pipeline.reviews import start_apply_run, transition_bundle

        op = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == rev.revision_id)
        )
        assert op is not None
        op.decision = "accepted"
        transition_bundle(session, rev.bundle_id, BundleState.READY)
        run = start_apply_run(session, rev.bundle_id, idempotency_key="test-apply-for-undo-cancel")
        # mark apply run as applied to allow undo display
        run.state = "applied"
        session.flush()
        from muzilla.db.models import ReviewUndoRun

        undo = ReviewUndoRun(
            review_bundle_id=rev.bundle_id,
            source_apply_run_id=run.id,
            idempotency_key="test-undo-cancel",
            state="undoing",
            manifest={"job_ids": [], "files": []},
        )
        session.add(undo)
        session.flush()
        job = queue.enqueue(session, type="undo_review_bundle", payload={"undo_run_id": undo.id})
        undo.manifest = {**undo.manifest, "job_ids": [job.id]}
        from muzilla.db.models import Job

        j = session.get(Job, job.id)
        assert j is not None
        j.state = "cancelling"
        j.cancel_requested = True
        session.commit()
        undo_id = undo.id
    resp = client.get("/api/activity")
    assert resp.status_code == 200
    item = next((i for i in resp.json()["items"] if i["undo_run_id"] == undo_id), None)
    assert item is not None
    assert item["state"] == "cancelling"
    assert item["cancellable"] is False


def test_activity_pagination_keyset_stable(client: TestClient, migrated_db: Path) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        for i in range(5):
            queue.enqueue(session, type="scan", payload={"root": f"/music/p{i}"})
    # iterate with limit 2 until exhausted
    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        qs = "/api/activity?limit=2" + (f"&cursor={cursor}" if cursor else "")
        resp = client.get(qs)
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["id"] not in seen
            seen.add(item["id"])
        pages += 1
        cursor = data["next_cursor"]
        if cursor is None:
            break
        assert pages < 10  # prevent infinite loop
    assert len(seen) >= 5
    # invalid cursor should be treated as first page, not error
    resp = client.get("/api/activity?limit=2&cursor=invalid@@")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 2


def test_activity_pagination_reaches_beyond_300_records(
    client: TestClient, migrated_db: Path
) -> None:
    # Regression for source truncation: >300 records of a single source must remain
    # reachable via DB/source-level keyset merge, not in-memory cap.
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        for i in range(320):
            queue.enqueue(session, type="scan", payload={"root": f"/music/large/{i:04d}"})
    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        qs = "/api/activity?limit=50" + (f"&cursor={cursor}" if cursor else "")
        resp = client.get(qs)
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["id"] not in seen
            seen.add(item["id"])
        pages += 1
        cursor = data["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, f"too many pages {pages}"
    # All 320 scans plus any incidental activity must be visited; at minimum our 320 survive truncation.
    assert len(seen) >= 320, f"only {len(seen)} seen, expected >=320"


def test_activity_pagination_equal_timestamps_lexical_order(
    client: TestClient, migrated_db: Path
) -> None:
    # Regression for P1: equal created_at rows must not be skipped by
    # numeric-id ordering. Each source query must order by the same lexical
    # formatted activity-ID expression used by cursor filtering.
    from datetime import UTC, datetime

    from sqlalchemy import select

    from muzilla.db.models import Job

    fixed = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
    count = 310
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        for i in range(count):
            queue.enqueue(session, type="scan", payload={"root": f"/music/equal/{i:04d}"})
    with factory() as session:
        jobs = list(session.scalars(select(Job)))
        for j in jobs:
            j.created_at = fixed
            j.updated_at = fixed
        session.commit()
        # Activity only surfaces scan-family jobs (system retention hidden)
        expected_ids = {
            f"job:{j.id}"
            for j in jobs
            if j.type in ("scan", "rescan_track", "analyze_track", "detect_duplicates")
        }
    assert len(expected_ids) >= count, (
        f"expected at least {count} scan jobs, got {len(expected_ids)}"
    )
    seen: set[str] = set()
    cursor: str | None = None
    pages = 0
    while True:
        qs = "/api/activity?limit=50" + (f"&cursor={cursor}" if cursor else "")
        resp = client.get(qs)
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["id"] not in seen, f"duplicate {item['id']}"
            seen.add(item["id"])
        pages += 1
        cursor = data["next_cursor"]
        if cursor is None:
            break
        assert pages < 20, f"too many pages {pages}"
    assert expected_ids.issubset(seen), f"missed {expected_ids - seen}"
    assert seen == expected_ids, f"missed {expected_ids - seen} extra {seen - expected_ids}"
