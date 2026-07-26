from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import ImportSession, ImportTask, Job, JobEvent


def test_job_roundtrip(db_session: Session) -> None:
    job = Job(type="scan", payload={"root": "/music"})
    db_session.add(job)
    db_session.commit()

    fetched = db_session.get(Job, job.id)
    assert fetched is not None
    assert fetched.state == "pending"
    assert fetched.priority == 0
    assert fetched.payload == {"root": "/music"}
    assert fetched.cancel_requested is False
    assert fetched.attempts == 0


def test_job_event_seq_and_cascade(db_session: Session) -> None:
    job = Job(type="scan", payload={})
    db_session.add(job)
    db_session.commit()

    e1 = JobEvent(job_id=job.id, seq=1, kind="progress", payload={"current": 1})
    e2 = JobEvent(job_id=job.id, seq=2, kind="progress", payload={"current": 2})
    db_session.add_all([e1, e2])
    db_session.commit()

    events = (
        db_session.query(JobEvent)
        .filter_by(job_id=job.id)
        .order_by(JobEvent.seq)
        .all()
    )
    assert [e.seq for e in events] == [1, 2]

    db_session.delete(job)
    db_session.commit()
    remaining = db_session.query(JobEvent).filter_by(job_id=job.id).all()
    assert remaining == []


def test_job_parent_fk_set_null_on_delete(db_session: Session) -> None:
    parent = Job(type="import", payload={})
    db_session.add(parent)
    db_session.commit()

    child = Job(type="scan", payload={}, parent_job_id=parent.id)
    db_session.add(child)
    db_session.commit()
    child_id = child.id

    db_session.delete(parent)
    db_session.commit()
    db_session.expire_all()

    refreshed = db_session.get(Job, child_id)
    assert refreshed is not None
    assert refreshed.parent_job_id is None


def test_import_session_and_tasks_roundtrip(db_session: Session) -> None:
    session_row = ImportSession(library_root="/music", stats={})
    db_session.add(session_row)
    db_session.commit()

    tasks = [
        ImportTask(import_session_id=session_row.id, stage=stage, seq=i)
        for i, stage in enumerate(("scan", "fingerprint", "group", "match"))
    ]
    db_session.add_all(tasks)
    db_session.commit()

    fetched = db_session.get(ImportSession, session_row.id)
    assert fetched is not None
    assert [t.stage for t in sorted(fetched.tasks, key=lambda t: t.seq)] == [
        "scan",
        "fingerprint",
        "group",
        "match",
    ]

    db_session.delete(fetched)
    db_session.commit()
    remaining = db_session.query(ImportTask).filter_by(import_session_id=session_row.id).all()
    assert remaining == []


def test_changeset_links_to_import_session(db_session: Session) -> None:
    from muzilla.db.models import ChangeSet

    session_row = ImportSession(library_root="/music", stats={})
    db_session.add(session_row)
    db_session.commit()

    cs = ChangeSet(
        title="test",
        source="match_proposal",
        scope_type="group",
        import_session_id=session_row.id,
    )
    db_session.add(cs)
    db_session.commit()

    fetched = db_session.get(ChangeSet, cs.id)
    assert fetched is not None
    assert fetched.import_session_id == session_row.id

    db_session.delete(session_row)
    db_session.commit()
    db_session.expire_all()
    refreshed = db_session.get(ChangeSet, cs.id)
    assert refreshed is not None
    assert refreshed.import_session_id is None
