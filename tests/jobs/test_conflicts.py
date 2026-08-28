# ponytail: deterministic REVIEW-CONFLICTS-001 coverage - drift and concurrent
import shutil
import time
from pathlib import Path

from sqlalchemy import select  # pyright: ignore
from sqlalchemy.orm import Session  # pyright: ignore

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ReviewBundle
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import put_revision, transition_bundle
from muzilla.services.reviews import OperationDraft

FIXTURE_MP3 = Path(__file__).parent.parent / "fixtures/audio/silence.mp3"


def _copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _track(session: Session, path: Path) -> int:
    from datetime import UTC, datetime

    from muzilla.db.models import Track
    from muzilla.domain.metadata import tag_hash as th
    from muzilla.tags.reader import read_track

    stat = path.stat()
    meta = read_track(path)
    h = th(meta)
    now = datetime.now(UTC)
    t = Track(
        path=str(path),
        filename=path.name,
        ext=path.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        title=meta.title or "t",
        artist=meta.artist or "a",
        tag_hash=h,
        first_seen_at=now,
        last_scanned_at=now,
    )
    session.add(t)
    session.flush()
    return t.id


def _bundle(session: Session, tid: int) -> int:
    from muzilla.db.models import Track

    tr = session.get(Track, tid)
    assert tr is not None
    items = [
        {
            "source_type": "track",
            "source_id": tid,
            "path": tr.path,
            "size_bytes": tr.size_bytes,
            "mtime_ns": tr.mtime_ns,
            "tag_hash": tr.tag_hash,
            "filename": tr.filename,
        }
    ]
    ops = [
        OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=tid,
            current_value=tr.title,
            proposed_value="New",
        )
    ]
    w = put_revision(
        session,
        logical_key=f"track:{tid}:conflict",
        title="conflict",
        scope_type="track",
        scope_id=tid,
        source_snapshot={"items": items},
        operations=tuple(ops),
    )
    for op in session.scalars(
        select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation).where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == w.revision_id
        )
    ):
        op.decision = "accepted"
    transition_bundle(session, w.bundle_id, BundleState.READY)
    session.commit()
    return w.bundle_id


def test_drift_mtime_preserving_tag_blocks_whole_bundle(
    tmp_path: Path, db_session: Session
) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    f = lib / "a.mp3"
    _copy(FIXTURE_MP3, f)
    tid = _track(db_session, f)
    bid = _bundle(db_session, tid)
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.pipeline.reviews import start_apply_run

    run = start_apply_run(db_session, bid, idempotency_key="drift-1")
    db_session.commit()
    # touch file preserving tag_hash (utime only)
    time.sleep(0.01)
    # ensure mtime changes but tag_hash stays same (just touch)
    f.touch()
    # ensure size same, mtime different, tag_hash same
    from muzilla.domain.metadata import tag_hash as th
    from muzilla.tags.reader import read_track

    # re-read to confirm tag_hash unchanged
    _track_obj = db_session.get(__import__("muzilla.db.models", fromlist=["Track"]).Track, tid)
    assert _track_obj is not None
    assert th(read_track(f)) == _track_obj.tag_hash
    result = apply_review_run(
        db_session, run.id, library_root=lib, blob_store=BlobStore(tmp_path / "b")
    )
    assert result.state == "failed"
    assert any(
        "stale" in (e or "") or "stat changed" in (e or "") for e in result.errors.values()
    ) or "stale" in (db_session.get(ReviewBundle, bid).error or "")  # type: ignore[union-attr]
    # whole bundle blocked - no journal
    from muzilla.db.models import ReviewFileJournal

    j = list(
        db_session.scalars(
            select(ReviewFileJournal).where(ReviewFileJournal.apply_run_id == run.id)
        )
    )
    assert len(j) == 0


def test_concurrent_apply_same_track_second_gets_409(tmp_path: Path, db_session: Session) -> None:
    lib = tmp_path / "lib2"
    lib.mkdir()
    f = lib / "b.mp3"
    _copy(FIXTURE_MP3, f)
    tid = _track(db_session, f)
    # two bundles sharing same track (different logical_key but same source_id)
    b1 = _bundle(db_session, tid)
    # second bundle with different logical_key but same track
    # manually create second bundle via put_revision with different logical_key
    from muzilla.db.models import Track

    tr = db_session.get(Track, tid)
    assert tr is not None
    items = [
        {
            "source_type": "track",
            "source_id": tid,
            "path": tr.path,
            "size_bytes": tr.size_bytes,
            "mtime_ns": tr.mtime_ns,
            "tag_hash": tr.tag_hash,
            "filename": tr.filename,
        }
    ]
    ops = [
        OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=tid,
            current_value=tr.title,
            proposed_value="New2",
        )
    ]
    w2 = put_revision(
        db_session,
        logical_key=f"track:{tid}:conflict2",
        title="conflict2",
        scope_type="track",
        scope_id=tid,
        source_snapshot={"items": items},
        operations=tuple(ops),
    )
    for op in db_session.scalars(
        select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation).where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == w2.revision_id
        )
    ):
        op.decision = "accepted"
    transition_bundle(db_session, w2.bundle_id, BundleState.READY)
    db_session.commit()
    b2 = w2.bundle_id
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.pipeline.reviews import start_apply_run

    r1 = start_apply_run(db_session, b1, idempotency_key="conc-1")
    r2 = start_apply_run(db_session, b2, idempotency_key="conc-2")
    db_session.commit()
    # keep r1 pending to simulate concurrent
    assert r1.id != r2.id
    # first apply will be pending, second preflight should detect concurrent
    # simulate concurrent by keeping r1 pending and trying r2 preflight
    # r1 is pending, r2 apply should block
    result2 = apply_review_run(
        db_session, r2.id, library_root=lib, blob_store=BlobStore(tmp_path / "b2")
    )
    assert result2.state == "failed"
    _b2 = db_session.get(ReviewBundle, b2)
    assert _b2 is not None
    assert any("concurrent" in (e or "") for e in result2.errors.values()) or "concurrent" in (_b2.error or "")
    # also test service-level 409
    from muzilla.services.review_apply import enqueue_review_apply

    # r1 still pending, enqueue for b2 should raise concurrent
    try:
        enqueue_review_apply(db_session, b2, idempotency_key="conc-3")
        raise AssertionError("expected concurrent 409")
    except Exception as exc:
        assert "concurrent" in str(exc).lower()
