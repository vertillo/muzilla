from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.jobs import queue
from muzilla.services import imports as imports_service


def test_start_import_creates_session_tasks_and_job(db_session: Session) -> None:
    summary = imports_service.start_import(db_session, "/music")

    assert summary.library_root == "/music"
    assert summary.state == "pending"
    assert summary.job_id is not None

    job = queue.get_job(db_session, summary.job_id)
    assert job is not None
    assert job.type == "import"
    assert job.payload == {"import_session_id": summary.id}

    detail = imports_service.get_import_session(db_session, summary.id)
    assert detail is not None
    assert [t.stage for t in detail.tasks] == ["scan", "fingerprint", "group", "match"]
    assert all(t.state == "pending" for t in detail.tasks)


def test_get_import_session_missing_returns_none(db_session: Session) -> None:
    assert imports_service.get_import_session(db_session, 99999) is None


def test_get_import_session_lists_produced_changesets(db_session: Session) -> None:
    from muzilla.db.models import ChangeSet

    summary = imports_service.start_import(db_session, "/music")
    cs = ChangeSet(
        title="test", source="match_proposal", scope_type="group",
        import_session_id=summary.id,
    )
    db_session.add(cs)
    db_session.commit()

    detail = imports_service.get_import_session(db_session, summary.id)
    assert detail is not None
    assert detail.changeset_ids == (cs.id,)


def test_resume_import_reuses_existing_tasks(db_session: Session) -> None:
    summary = imports_service.start_import(db_session, "/music")
    original_detail = imports_service.get_import_session(db_session, summary.id)
    assert original_detail is not None
    original_task_count = len(original_detail.tasks)

    resumed = imports_service.resume_import(db_session, summary.id)
    assert resumed.id == summary.id
    assert resumed.job_id is not None
    assert resumed.job_id != summary.job_id

    detail = imports_service.get_import_session(db_session, summary.id)
    assert detail is not None
    assert len(detail.tasks) == original_task_count


def test_resume_import_missing_session_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        imports_service.resume_import(db_session, 99999)
