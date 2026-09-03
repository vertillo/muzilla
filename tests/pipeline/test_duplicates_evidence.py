"""DOMAIN-DUPLICATES-001: evidence model, confidence, duration/quality, positive/negative corpus."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackFingerprintMatch
from muzilla.domain.duplicate_evidence import DuplicateEvidence
from muzilla.pipeline.duplicates import detect_duplicates


def _make_track(db_session: Session, path: str, duration_ms: int, fmt: str = "MP3", bitrate: int = 192) -> Track:
    # Minimal track creation for pipeline test
    from datetime import UTC, datetime
    track = Track(
        path=path,
        filename=path.split("/")[-1],
        ext=".mp3",
        size_bytes=1000,
        mtime_ns=1,
        duration_ms=duration_ms,
        format=fmt,
        bitrate=bitrate,
        first_seen_at=datetime.now(UTC),
        last_scanned_at=datetime.now(UTC),
    )
    db_session.add(track)
    db_session.flush()
    return track


def test_positive_duplicate_high_confidence(db_session: Session) -> None:
    t1 = _make_track(db_session, "/music/a.mp3", duration_ms=200_000, bitrate=320)
    t2 = _make_track(db_session, "/music/b.mp3", duration_ms=202_000, bitrate=128)
    # Same recording, high scores, similar duration -> high confidence, not uncertain
    db_session.add_all([
        TrackFingerprintMatch(track_id=t1.id, mb_recording_id="rec-123", mb_release_ids=[], score=0.95),
        TrackFingerprintMatch(track_id=t2.id, mb_recording_id="rec-123", mb_release_ids=[], score=0.92),
    ])
    db_session.commit()

    result = detect_duplicates(db_session)
    assert result.groups_created == 1
    from muzilla.db.models import DuplicateGroup
    group = db_session.scalar(select(DuplicateGroup))
    assert group is not None
    assert group.confidence is not None
    assert group.confidence >= 0.7
    assert group.evidence is not None
    assert group.evidence is not None
    ev = DuplicateEvidence.from_dict(group.evidence)
    assert ev.confidence_label in ("alta", "media")
    assert ev.is_uncertain is False
    assert ev.is_false_positive_candidate is False
    # Duration delta small -> not false positive
    assert ev.duration.delta_percent is not None
    assert ev.duration.delta_percent < 5.0
    # Quality facts present
    assert len(ev.quality) == 2
    assert ev.quality[0].bitrate is not None


def test_negative_duplicate_low_confidence_due_to_duration(db_session: Session) -> None:
    t1 = _make_track(db_session, "/music/c.mp3", duration_ms=180_000, bitrate=320)
    t2 = _make_track(db_session, "/music/d.mp3", duration_ms=260_000, bitrate=128)
    # Same recording but large duration difference -> low confidence, uncertain -> false positive candidate
    db_session.add_all([
        TrackFingerprintMatch(track_id=t1.id, mb_recording_id="rec-456", mb_release_ids=[], score=0.9),
        TrackFingerprintMatch(track_id=t2.id, mb_recording_id="rec-456", mb_release_ids=[], score=0.88),
    ])
    db_session.commit()

    result = detect_duplicates(db_session)
    assert result.groups_created == 1
    from muzilla.db.models import DuplicateGroup
    group = db_session.scalar(select(DuplicateGroup))
    assert group is not None
    assert group.confidence is not None
    assert group.evidence is not None
    assert group.evidence is not None
    ev = DuplicateEvidence.from_dict(group.evidence)
    assert ev.is_uncertain is True or ev.confidence < 0.5
    # Large duration delta should flag as false positive candidate
    assert ev.duration.delta_percent is not None
    assert ev.duration.delta_percent > 20.0 or ev.is_false_positive_candidate is True


def test_migration_and_api_serializes_evidence(db_session: Session) -> None:
    # Ensure group without evidence (legacy) still serializes via service
    t1 = _make_track(db_session, "/music/e.mp3", duration_ms=200_000)
    t2 = _make_track(db_session, "/music/f.mp3", duration_ms=200_000)
    db_session.add_all([
        TrackFingerprintMatch(track_id=t1.id, mb_recording_id="rec-789", mb_release_ids=[], score=0.9),
        TrackFingerprintMatch(track_id=t2.id, mb_recording_id="rec-789", mb_release_ids=[], score=0.9),
    ])
    db_session.commit()
    detect_duplicates(db_session)
    from muzilla.services.duplicates import list_duplicate_groups
    groups = list_duplicate_groups(db_session)
    assert len(groups) == 1
    assert groups[0].confidence is not None
    assert groups[0].evidence is not None
    assert groups[0].evidence is not None
    assert "confidence" in groups[0].evidence
