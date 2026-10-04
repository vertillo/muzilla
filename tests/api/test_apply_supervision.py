from __future__ import annotations

import asyncio
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from muzilla.api.app import create_app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import (
    ApplyRun,
    Job,
    Operation,
    ReviewFileJournal,
    ReviewUndoRun,
    Track,
    WorkUnit,
)
from muzilla.domain.metadata import tag_hash
from muzilla.domain.reviews import BundleState
from muzilla.jobs import queue
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.pipeline.reviews import OperationDraft, put_revision, transition_bundle
from muzilla.services.db import get_session_factory
from muzilla.tags.reader import read_track

FIXTURE_AUDIO = Path(__file__).parent.parent / "fixtures" / "audio" / "silence.mp3"


async def test_apply_and_undo_keep_api_responsive_and_preserve_late_cancel(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv(
        "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers")
    )
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")
    monkeypatch.setenv("MUZILLA_JOBS__POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MUZILLA_JOBS__JOB_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("MUZILLA_JOBS__LEASE_SECONDS", "3")
    monkeypatch.setenv("MUZILLA_RETENTION__ENABLED", "false")

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    source = library / "original.mp3"
    destination = library / "renamed.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    meta = read_track(source)
    stat = source.stat()
    now = datetime.now(UTC)
    with factory() as session:
        track = Track(
            path=str(source),
            filename=source.name,
            ext=".mp3",
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            title=meta.title or "Fixture title",
            artist=meta.artist or "Fixture artist",
            tag_hash=tag_hash(meta),
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.flush()
        revision = put_revision(
            session,
            logical_key=f"track:{track.id}",
            title="Move disposable fixture",
            scope_type="track",
            scope_id=track.id,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": track.id,
                        "path": str(source),
                        "filename": source.name,
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "tag_hash": track.tag_hash,
                    }
                ]
            },
            operations=(
                OperationDraft(
                    kind="move_file",
                    field="path",
                    target_type="track",
                    target_id=track.id,
                    current_value=str(source),
                    proposed_value=str(destination),
                ),
            ),
        )
        operation = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
        )
        assert operation is not None
        operation.decision = "accepted"
        transition_bundle(session, revision.bundle_id, BundleState.READY)
        session.commit()
        track_id = track.id
        bundle_id = revision.bundle_id

    from muzilla.changes import writer
    from muzilla.jobs.handlers import apply as apply_handler
    from muzilla.jobs.progress import ProgressReporter

    original_move = writer._move_no_clobber
    original_apply_sync = apply_handler._apply_sync
    original_progress_update = ProgressReporter.update
    apply_started = Event()
    apply_release = Event()
    apply_committed = Event()
    apply_result_release = Event()

    def block_apply_move(source_path: Path, destination_path: Path, *, same_file: bool) -> None:
        apply_started.set()
        assert apply_release.wait(timeout=10)
        original_move(source_path, destination_path, same_file=same_file)

    def block_after_apply_commit(*args: Any, **kwargs: Any) -> dict[str, object]:
        original_apply_sync(*args, **kwargs)
        apply_committed.set()
        assert apply_result_release.wait(timeout=10)
        raise RuntimeError("apply response interrupted after durable commit")

    def interrupt_undo_response(
        reporter: ProgressReporter,
        current: int,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        if message == "review restore complete":
            raise asyncio.CancelledError
        original_progress_update(reporter, current, total=total, message=message)

    with monkeypatch.context() as patch:
        patch.setattr(writer, "_move_no_clobber", block_apply_move)
        patch.setattr(apply_handler, "_apply_sync", block_after_apply_commit)
        patch.setattr(ProgressReporter, "update", interrupt_undo_response)
        with TestClient(create_app()) as client:
            csrf_token = client.get("/api/auth/status").json()["csrf_token"]
            client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf_token})
            applied = client.post(
                f"/api/reviews/{bundle_id}/apply",
                headers={"Idempotency-Key": "apply-responsive-fixture"},
                json={"backup": False},
            )
            assert applied.status_code == 202
            apply_run_id = applied.json()["apply_run_id"]
            apply_job_id = applied.json()["job_id"]
            assert apply_started.wait(timeout=5)

            def exercise_api_while_blocked(job_id: int, request_cancel: bool) -> dict[str, Any]:
                health = client.get("/api/health")
                detail = client.get(f"/api/jobs/{job_id}")
                activity = client.get("/api/activity")
                cancel = client.post(f"/api/jobs/{job_id}/cancel") if request_cancel else None
                return {
                    "health": health,
                    "detail": detail,
                    "activity": activity,
                    "cancel": cancel,
                }

            initial_lease: datetime | None
            with factory() as session:
                active_job = session.get(Job, apply_job_id)
                assert active_job is not None and active_job.state == "running"
                initial_lease = active_job.lease_until
            await asyncio.sleep(1.1)
            with factory() as session:
                refreshed_job = session.get(Job, apply_job_id)
                assert refreshed_job is not None and refreshed_job.lease_until is not None
                assert initial_lease is not None
                assert refreshed_job.lease_until.replace(tzinfo=UTC) > initial_lease.replace(
                    tzinfo=UTC
                )

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(exercise_api_while_blocked, apply_job_id, False)
                try:
                    api_results = pending.result(timeout=1)
                finally:
                    apply_release.set()
            assert api_results["health"].status_code == 200
            assert api_results["detail"].status_code == 200
            assert api_results["detail"].json()["state"] == "running"
            assert api_results["activity"].status_code == 200
            assert api_results["cancel"] is None
            assert await asyncio.to_thread(apply_committed.wait, 5)

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(exercise_api_while_blocked, apply_job_id, True)
                try:
                    late_apply_api = pending.result(timeout=1)
                finally:
                    apply_result_release.set()
            assert late_apply_api["cancel"] is not None
            assert late_apply_api["cancel"].status_code == 200
            assert late_apply_api["cancel"].json()["state"] == "cancelling"
            late_apply_activity = client.get("/api/activity").json()["items"]
            committed_apply_item = next(
                item for item in late_apply_activity if item["apply_run_id"] == apply_run_id
            )
            assert committed_apply_item["state"] == "succeeded"
            assert committed_apply_item["result"]["state"] == "applied"

            apply_done = await _wait_for_terminal_job(client, apply_job_id)
            assert apply_done["state"] == "succeeded"
            assert apply_done["result"]["state"] == "applied"
            assert source.exists() is False and destination.exists()
            with factory() as session:
                applied_job = session.get(Job, apply_job_id)
                assert applied_job is not None and not applied_job.cancel_requested
                apply_events = queue.list_events_after(session, apply_job_id, after_seq=0)
            apply_states = [
                event.payload["state"] for event in apply_events if event.kind == "state"
            ]
            assert apply_states[-2:] == ["cancelling", "succeeded"]
            apply_activity = client.get("/api/activity").json()["items"]
            applied_item = next(
                item for item in apply_activity if item["apply_run_id"] == apply_run_id
            )
            assert applied_item["state"] == "succeeded"
            assert applied_item["result"]["state"] == "applied"

            original_undo_move = original_move
            original_undo_sync = apply_handler._undo_sync
            undo_started = Event()
            undo_release = Event()
            undo_committed = Event()
            undo_result_release = Event()

            def block_undo_move(
                source_path: Path, destination_path: Path, *, same_file: bool
            ) -> None:
                undo_started.set()
                assert undo_release.wait(timeout=10)
                original_undo_move(source_path, destination_path, same_file=same_file)

            def block_after_undo_commit(*args: Any, **kwargs: Any) -> dict[str, object]:
                result = original_undo_sync(*args, **kwargs)
                undo_committed.set()
                assert undo_result_release.wait(timeout=10)
                return result

            patch.setattr(writer, "_move_no_clobber", block_undo_move)
            patch.setattr(apply_handler, "_undo_sync", block_after_undo_commit)
            undone = client.post(
                f"/api/reviews/{bundle_id}/undo",
                headers={"Idempotency-Key": "undo-responsive-fixture"},
                json={"apply_run_id": apply_run_id, "backup": False},
            )
            assert undone.status_code == 202
            undo_run_id = undone.json()["undo_run_id"]
            undo_job_id = undone.json()["job_id"]
            assert undo_started.wait(timeout=5)
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(exercise_api_while_blocked, undo_job_id, False)
                try:
                    undo_api_results = pending.result(timeout=1)
                finally:
                    undo_release.set()
            assert undo_api_results["health"].status_code == 200
            assert undo_api_results["detail"].status_code == 200
            assert undo_api_results["detail"].json()["state"] == "running"
            assert undo_api_results["activity"].status_code == 200
            assert undo_api_results["cancel"] is None
            assert await asyncio.to_thread(undo_committed.wait, 5)

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(exercise_api_while_blocked, undo_job_id, True)
                try:
                    late_undo_api = pending.result(timeout=1)
                finally:
                    undo_result_release.set()
            assert late_undo_api["cancel"] is not None
            assert late_undo_api["cancel"].status_code == 200
            assert late_undo_api["cancel"].json()["state"] == "cancelling"
            late_undo_activity = client.get("/api/activity").json()["items"]
            committed_undo_item = next(
                item for item in late_undo_activity if item["undo_run_id"] == undo_run_id
            )
            assert committed_undo_item["state"] == "succeeded"
            assert committed_undo_item["result"]["state"] == "undone"

            undo_done = await _wait_for_terminal_job(client, undo_job_id)
            assert undo_done["state"] == "succeeded"
            assert undo_done["result"]["state"] == "undone"
            assert source.exists() and destination.exists() is False
            with factory() as session:
                undo_job = session.get(Job, undo_job_id)
                assert undo_job is not None and not undo_job.cancel_requested
                undo_events = queue.list_events_after(session, undo_job_id, after_seq=0)
            undo_states = [event.payload["state"] for event in undo_events if event.kind == "state"]
            assert undo_states[-2:] == ["cancelling", "succeeded"]
            undo_activity = client.get("/api/activity").json()["items"]
            undone_item = next(item for item in undo_activity if item["undo_run_id"] == undo_run_id)
            assert undone_item["state"] == "succeeded"
            assert undone_item["result"]["state"] == "undone"
            with factory() as session:
                final_track = session.get(Track, track_id)
                assert final_track is not None and final_track.path == str(source)


@pytest.mark.parametrize(
    ("track_count", "expected_job_state"), [(2, "succeeded"), (3, "cancelled")]
)
async def test_multi_file_tag_undo_releases_writer_lock_between_safe_file_checkpoints(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    track_count: int,
    expected_job_state: str,
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv(
        "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers")
    )
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")
    monkeypatch.setenv("MUZILLA_JOBS__POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MUZILLA_JOBS__JOB_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("MUZILLA_JOBS__LEASE_SECONDS", "3")
    monkeypatch.setenv("MUZILLA_RETENTION__ENABLED", "false")

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is required to create realistic encoded audio"
    realistic_audio = tmp_path / "thirty-second.mp3"
    generated = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=30:sample_rate=44100",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(realistic_audio),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert generated.returncode == 0 and realistic_audio.exists(), generated.stderr
    source_snapshot_items: list[dict[str, object]] = []
    operations: list[OperationDraft] = []
    now = datetime.now(UTC)
    with factory() as session:
        group = WorkUnit(
            key=f"undo-responsive-{track_count}", album="Checkpoint Album", kind="album"
        )
        session.add(group)
        session.flush()
        for index in range(track_count):
            source = library / f"source-{index}.mp3"
            # Each 30-second encoded MP3 is a realistic-size disposable audio
            # fixture, exercising actual tag restoration rather than tiny moves.
            shutil.copy(realistic_audio, source)
            meta = read_track(source)
            stat = source.stat()
            track = Track(
                path=str(source),
                filename=source.name,
                ext=".mp3",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                title=meta.title or f"Original title {index}",
                artist="Regression artist",
                album="Checkpoint Album",
                tag_hash=tag_hash(meta),
                work_unit_id=group.id,
                first_seen_at=now,
                last_scanned_at=now,
            )
            session.add(track)
            session.flush()
            source_snapshot_items.append(
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": str(source),
                    "filename": source.name,
                    "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "tag_hash": track.tag_hash,
                }
            )
            operations.append(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=track.id,
                    current_value=track.title,
                    proposed_value=f"Applied title {index}",
                )
            )
        revision = put_revision(
            session,
            logical_key=f"group:undo-responsive-{track_count}",
            title="Undo multi-file tag restoration",
            scope_type="group",
            scope_id=group.id,
            source_snapshot={"items": source_snapshot_items},
            operations=tuple(operations),
        )
        for operation in session.scalars(
            select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
        ):
            operation.decision = "accepted"
        transition_bundle(session, revision.bundle_id, BundleState.READY)
        session.commit()
        bundle_id = revision.bundle_id
        track_ids = [cast(int, item["source_id"]) for item in source_snapshot_items]

    app = create_app()
    service_engine: Engine | None = None
    with TestClient(app) as client:
        service_factory = get_session_factory(app.state.config)
        service_engine = cast(Engine, service_factory.kw["bind"])
        csrf_token = client.get("/api/auth/status").json()["csrf_token"]
        client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf_token})
        applied = client.post(
            f"/api/reviews/{bundle_id}/apply",
            headers={"Idempotency-Key": f"apply-large-tags-{track_count}"},
            json={"backup": False},
        )
        assert applied.status_code == 202
        apply_job_id = applied.json()["job_id"]
        apply_done = await _wait_for_terminal_job(client, apply_job_id)
        assert apply_done["state"] == "succeeded"
        apply_run_id = applied.json()["apply_run_id"]
        with factory() as session:
            applied_tracks = [session.get(Track, track_id) for track_id in track_ids]
            assert all(
                track is not None and track.title == f"Applied title {index}"
                for index, track in enumerate(applied_tracks)
            ), (
                apply_done,
                [(track.title if track is not None else None) for track in applied_tracks],
            )

        from muzilla.changes import writer

        original_restore = writer.restore_from_before_blob
        restore_lock = Lock()
        restore_calls = 0
        second_restore_started = Event()
        second_restore_release = Event()
        second_restore_finished = Event()

        def block_second_restore(*args: Any, **kwargs: Any) -> None:
            nonlocal restore_calls
            with restore_lock:
                restore_calls += 1
                call_number = restore_calls
            try:
                if call_number == 2:
                    second_restore_started.set()
                    assert second_restore_release.wait(timeout=15)
                original_restore(*args, **kwargs)
            finally:
                if call_number == 2:
                    second_restore_finished.set()

        with monkeypatch.context() as patch:
            patch.setattr(writer, "restore_from_before_blob", block_second_restore)
            undone = client.post(
                f"/api/reviews/{bundle_id}/undo",
                headers={"Idempotency-Key": f"undo-large-tags-{track_count}"},
                json={"apply_run_id": apply_run_id, "backup": False},
            )
            assert undone.status_code == 202
            undo_run_id = undone.json()["undo_run_id"]
            undo_job_id = undone.json()["job_id"]
            assert await asyncio.to_thread(second_restore_started.wait, 5)
            try:
                with factory() as session:
                    active_job = session.get(Job, undo_job_id)
                    assert active_job is not None and active_job.state == "running"
                    initial_lease = active_job.lease_until
                    journals = list(
                        session.scalars(
                            select(ReviewFileJournal)
                            .where(ReviewFileJournal.apply_run_id == apply_run_id)
                            .order_by(ReviewFileJournal.id.desc())
                        )
                    )
                    assert sum(journal.state == "rolled_back" for journal in journals) == 1
                    assert sum(journal.state == "writing" for journal in journals) == 1
                    assert sum(journal.state == "done" for journal in journals) == track_count - 2

                await asyncio.sleep(1.1)
                with factory() as session:
                    refreshed_job = session.get(Job, undo_job_id)
                    assert refreshed_job is not None and refreshed_job.lease_until is not None
                    assert initial_lease is not None
                    assert refreshed_job.lease_until.replace(tzinfo=UTC) > initial_lease.replace(
                        tzinfo=UTC
                    )

                def exercise_api_while_blocked() -> dict[str, Any]:
                    return {
                        "health": client.get("/api/health"),
                        "detail": client.get(f"/api/jobs/{undo_job_id}"),
                        "activity": client.get("/api/activity"),
                        "cancel": client.post(f"/api/jobs/{undo_job_id}/cancel"),
                    }

                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(exercise_api_while_blocked)
                    api_results = pending.result(timeout=2)
                assert api_results["health"].status_code == 200
                assert api_results["detail"].status_code == 200
                assert api_results["detail"].json()["state"] == "running"
                assert api_results["activity"].status_code == 200
                active_undo = next(
                    item
                    for item in api_results["activity"].json()["items"]
                    if item["undo_run_id"] == undo_run_id
                )
                assert active_undo["state"] == "running"
                assert api_results["cancel"].status_code == 200
                assert api_results["cancel"].json()["state"] == "cancelling"
            finally:
                second_restore_release.set()

            result = await _wait_for_terminal_job(client, undo_job_id)
            assert result["state"] == expected_job_state
            assert second_restore_finished.is_set()
            with factory() as session:
                undo_run = session.get(ReviewUndoRun, undo_run_id)
                restored = [session.get(Track, track_id) for track_id in track_ids]
                assert undo_run is not None
                if expected_job_state == "succeeded":
                    assert undo_run.state == "undone"
                    assert all(
                        track is not None and track.title != f"Applied title {index}"
                        for index, track in enumerate(restored)
                    )
                else:
                    assert undo_run.state == "failed"
                    assert undo_run.result is not None
                    assert undo_run.result["cancelled"] is True
                    assert undo_run.result["recovery_required"] is True
                    assert undo_run.error is not None
                    assert undo_run.error.startswith("recovery_required:")
                    assert result["result"]["recovery_required"] is True
                    undo_files = undo_run.result.get("files")
                    assert isinstance(undo_files, list)
                    assert (
                        sum(
                            isinstance(file, dict) and file.get("state") == "undone"
                            for file in undo_files
                        )
                        == 2
                    )
                    assert restored[0] is not None
                    assert restored[0].title == "Applied title 0"
                    assert all(
                        track is not None and track.title != f"Applied title {index}"
                        for index, track in enumerate(restored[1:], start=1)
                    )

    assert cast(QueuePool, engine.pool).checkedout() == 0
    assert service_engine is not None
    assert cast(QueuePool, service_engine.pool).checkedout() == 0
    engine.dispose()


async def test_apply_rollback_releases_writer_lock_before_slow_tag_restore(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv(
        "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers")
    )
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")
    monkeypatch.setenv("MUZILLA_JOBS__POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MUZILLA_JOBS__JOB_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("MUZILLA_JOBS__LEASE_SECONDS", "3")
    monkeypatch.setenv("MUZILLA_JOBS__WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("MUZILLA_RETENTION__ENABLED", "false")

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None, "ffmpeg is required to create realistic encoded audio"
    realistic_audio = tmp_path / "thirty-second.mp3"
    generated = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=30:sample_rate=44100",
            "-ac",
            "1",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(realistic_audio),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert generated.returncode == 0 and realistic_audio.exists(), generated.stderr

    sources = [library / f"source-{index}.mp3" for index in range(2)]
    destinations = [library / f"renamed-{index}.mp3" for index in range(2)]
    source_snapshot_items: list[dict[str, object]] = []
    operations: list[OperationDraft] = []
    now = datetime.now(UTC)
    with factory() as session:
        group = WorkUnit(key="apply-rollback-contention", album="Rollback Album", kind="album")
        session.add(group)
        session.flush()
        for index, source in enumerate(sources):
            shutil.copy(realistic_audio, source)
            meta = read_track(source)
            stat = source.stat()
            track = Track(
                path=str(source),
                filename=source.name,
                ext=".mp3",
                size_bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                title=meta.title or f"Original title {index}",
                artist="Rollback artist",
                album="Rollback Album",
                tag_hash=tag_hash(meta),
                work_unit_id=group.id,
                first_seen_at=now,
                last_scanned_at=now,
            )
            session.add(track)
            session.flush()
            source_snapshot_items.append(
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": str(source),
                    "filename": source.name,
                    "size_bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "tag_hash": track.tag_hash,
                }
            )
            operations.extend(
                (
                    OperationDraft(
                        kind="set_tag",
                        field="title",
                        target_type="track",
                        target_id=track.id,
                        current_value=track.title,
                        proposed_value=f"Applied title {index}",
                    ),
                    OperationDraft(
                        kind="move_file",
                        field="path",
                        target_type="track",
                        target_id=track.id,
                        current_value=str(source),
                        proposed_value=str(destinations[index]),
                    ),
                )
            )
        revision = put_revision(
            session,
            logical_key="group:apply-rollback-contention",
            title="Apply rollback writer contention",
            scope_type="group",
            scope_id=group.id,
            source_snapshot={"items": source_snapshot_items},
            operations=tuple(operations),
        )
        for operation in session.scalars(
            select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
        ):
            operation.decision = "accepted"
        transition_bundle(session, revision.bundle_id, BundleState.READY)
        session.commit()
        bundle_id = revision.bundle_id
        track_ids = [cast(int, item["source_id"]) for item in source_snapshot_items]

    unrelated_started = Event()
    unrelated_release = Event()
    unrelated_returned = Event()

    @register("test_apply_rollback_unrelated_async")
    async def handle_unrelated(
        session: Session,
        job: Job,
        progress: ProgressReporter,
        context: WorkerContext,
    ) -> dict[str, object]:
        unrelated_started.set()
        while not unrelated_release.is_set():
            await asyncio.sleep(0.01)
        unrelated_returned.set()
        return {"completed": True}

    from muzilla.changes import writer

    original_commit = Session.commit
    original_restore = writer.restore_from_before_blob
    first_file_committed = Event()
    release_first_file = Event()
    tag_restore_started = Event()
    release_tag_restore = Event()
    tag_restore_finished = Event()
    first_track_id = track_ids[0]

    def pause_after_first_file_commit(session: Session) -> None:
        original_commit(session)
        if first_file_committed.is_set():
            return
        for entity in session.identity_map.values():
            if not isinstance(entity, ApplyRun):
                continue
            raw_files = entity.manifest.get("files")
            if isinstance(raw_files, list) and any(
                isinstance(entry, dict)
                and entry.get("track_id") == first_track_id
                and entry.get("state") == "applied"
                for entry in raw_files
            ):
                first_file_committed.set()
                assert release_first_file.wait(timeout=15)
                return

    def pause_tag_restore(*args: Any, **kwargs: Any) -> None:
        tag_restore_started.set()
        try:
            assert release_tag_restore.wait(timeout=15)
            original_restore(*args, **kwargs)
        finally:
            tag_restore_finished.set()

    app = create_app()
    service_engine: Engine | None = None
    with monkeypatch.context() as patch:
        patch.setattr(Session, "commit", pause_after_first_file_commit)
        patch.setattr(writer, "restore_from_before_blob", pause_tag_restore)
        with ExitStack() as cleanup:
            client = cleanup.enter_context(TestClient(app))
            cleanup.callback(release_first_file.set)
            cleanup.callback(release_tag_restore.set)
            cleanup.callback(unrelated_release.set)
            service_factory = get_session_factory(app.state.config)
            service_engine = cast(Engine, service_factory.kw["bind"])
            csrf_token = client.get("/api/auth/status").json()["csrf_token"]
            client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf_token})
            applied = client.post(
                f"/api/reviews/{bundle_id}/apply",
                headers={"Idempotency-Key": "apply-rollback-contention"},
                json={"backup": False},
            )
            assert applied.status_code == 202
            apply_run_id = applied.json()["apply_run_id"]
            apply_job_id = applied.json()["job_id"]
            assert await asyncio.to_thread(first_file_committed.wait, 5)

            with factory() as session:
                apply_job = session.get(Job, apply_job_id)
                assert apply_job is not None and apply_job.state == "running"
                initial_lease = apply_job.lease_until
                unrelated = queue.enqueue(
                    session, type="test_apply_rollback_unrelated_async", payload={}
                )
                unrelated_job_id = unrelated.id
            assert await asyncio.to_thread(unrelated_started.wait, 5)

            cancel = client.post(f"/api/jobs/{apply_job_id}/cancel")
            assert cancel.status_code == 200
            assert cancel.json()["state"] == "cancelling"
            await asyncio.sleep(0.1)
            release_first_file.set()
            assert await asyncio.to_thread(tag_restore_started.wait, 5)

            try:
                with factory() as session:
                    apply_job = session.get(Job, apply_job_id)
                    apply_run = session.get(ApplyRun, apply_run_id)
                    journals = list(
                        session.scalars(
                            select(ReviewFileJournal).where(
                                ReviewFileJournal.apply_run_id == apply_run_id,
                                ReviewFileJournal.track_id == first_track_id,
                            )
                        )
                    )
                    assert apply_job is not None and apply_job.state == "cancelling"
                    assert apply_run is not None and apply_run.state == "applying"
                    tag_journal = next(journal for journal in journals if journal.phase == "tags")
                    move_journal = next(journal for journal in journals if journal.phase == "move")
                    assert tag_journal.state == "done"
                    # The reversed move is a durable safe checkpoint before slow tag I/O.
                    assert move_journal.state == "rolled_back"

                await asyncio.sleep(1.1)
                with factory() as session:
                    refreshed_job = session.get(Job, apply_job_id)
                    assert refreshed_job is not None and refreshed_job.lease_until is not None
                    assert initial_lease is not None
                    assert refreshed_job.lease_until.replace(tzinfo=UTC) > initial_lease.replace(
                        tzinfo=UTC
                    )

                unrelated_release.set()
                assert await asyncio.to_thread(unrelated_returned.wait, 2)
                await asyncio.sleep(0.05)

                def exercise_api_during_rollback() -> dict[str, Any]:
                    return {
                        "health": client.get("/api/health"),
                        "apply": client.get(f"/api/jobs/{apply_job_id}"),
                        "unrelated": client.get(f"/api/jobs/{unrelated_job_id}"),
                        "activity": client.get("/api/activity"),
                        "cancel": client.post(f"/api/jobs/{apply_job_id}/cancel"),
                    }

                with ThreadPoolExecutor(max_workers=1) as executor:
                    pending = executor.submit(exercise_api_during_rollback)
                    try:
                        api_results = pending.result(timeout=2)
                    finally:
                        # Avoid waiting for SQLite's full busy timeout on a regression.
                        release_tag_restore.set()
                assert api_results["health"].status_code == 200
                assert api_results["apply"].status_code == 200
                assert api_results["apply"].json()["state"] == "cancelling"
                assert api_results["unrelated"].status_code == 200
                assert api_results["unrelated"].json()["state"] == "succeeded"
                assert api_results["activity"].status_code == 200
                assert api_results["cancel"].status_code == 200
                assert api_results["cancel"].json()["state"] == "cancelling"
            finally:
                release_tag_restore.set()
                unrelated_release.set()

            apply_done = await _wait_for_terminal_job(client, apply_job_id)
            unrelated_done = await _wait_for_terminal_job(client, unrelated_job_id)
            assert apply_done["state"] == "cancelled"
            assert unrelated_done["state"] == "succeeded"
            assert tag_restore_finished.is_set()
            with factory() as session:
                apply_run = session.get(ApplyRun, apply_run_id)
                tracks = [session.get(Track, track_id) for track_id in track_ids]
                assert apply_run is not None and apply_run.state == "failed"
                assert apply_run.result is not None
                assert apply_run.result["cancelled"] is True
                assert apply_run.result["recovery_required"] is False
                assert tracks[0] is not None and tracks[0].path == str(sources[0])
                assert tracks[0].title != "Applied title 0"
                assert tracks[1] is not None and tracks[1].path == str(sources[1])
                assert tracks[1].title != "Applied title 1"
            assert sources[0].exists() and not destinations[0].exists()
            assert sources[1].exists() and not destinations[1].exists()
    assert cast(QueuePool, engine.pool).checkedout() == 0
    assert service_engine is not None
    assert cast(QueuePool, service_engine.pool).checkedout() == 0
    engine.dispose()


async def test_startup_leaves_expired_external_apply_untouched_while_lock_is_held(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv(
        "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers")
    )
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")
    monkeypatch.setenv("MUZILLA_JOBS__POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("MUZILLA_RETENTION__ENABLED", "false")

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    from muzilla.db.models import ApplyRun
    from muzilla.domain.reviews import BundleState
    from muzilla.jobs.execution_lock import JobExecutionLock
    from muzilla.pipeline.reviews import start_apply_run

    with factory() as session:
        revision = put_revision(
            session,
            logical_key="track:external-apply-lock",
            title="External worker apply",
            scope_type="track",
            scope_id=1,
            source_snapshot={"items": []},
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=1,
                    current_value="before",
                    proposed_value="after",
                ),
            ),
        )
        operation = session.scalar(
            select(Operation).where(Operation.proposal_revision_id == revision.revision_id)
        )
        assert operation is not None
        operation.decision = "accepted"
        transition_bundle(session, revision.bundle_id, BundleState.READY)
        run = start_apply_run(session, revision.bundle_id, idempotency_key="external-worker")
        job = queue.enqueue(session, type="apply_review_bundle", payload={"apply_run_id": run.id})
        run.state = "applying"
        run.manifest = {**run.manifest, "job_ids": [job.id]}
        leased = queue.lease_next(session, worker_id="external-worker", lease_seconds=1)
        assert leased is not None
        leased.lease_until = datetime.now(UTC)
        session.commit()
        run_id = run.id
        job_id = job.id
        execution_lock = JobExecutionLock.try_for_session(session, job_id)
        assert execution_lock is not None

    from muzilla.changes import bundle_applier

    recovery_calls: list[int] = []

    def record_recovery(*args: Any, **kwargs: Any) -> int:
        recovery_calls.append(1)
        return 0

    monkeypatch.setattr(bundle_applier, "recover_apply_runs", record_recovery)
    try:
        with TestClient(create_app()):
            with factory() as session:
                active_job = session.get(Job, job_id)
                active_run = session.get(ApplyRun, run_id)
                assert active_job is not None and active_job.state == "running"
                assert active_run is not None and active_run.state == "applying"
            assert recovery_calls == []
    finally:
        execution_lock.close()
        engine.dispose()


async def _wait_for_terminal_job(client: TestClient, job_id: int) -> dict[str, Any]:
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline:
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        result = response.json()
        if result["state"] in {"succeeded", "failed", "cancelled"}:
            return cast(dict[str, Any], result)
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not reach a terminal state")
