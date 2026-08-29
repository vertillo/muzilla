"""Provenance and refresh confirmation for MATCHING-PROVENANCE-001 (ponytail)."""

from sqlalchemy.orm import Session

from muzilla.domain.metadata import TrackMeta
from muzilla.matching.engine import propose_for_group
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate


def _release(source: str, album: str, tracks: list[str], year: int = 1999) -> ReleaseCandidate:
    return ReleaseCandidate(
        source=source,
        ref=ProviderRef(provider=source, id=f"{source}-release"),
        album=album,
        album_artist="Artist",
        year=year,
        tracks=tuple(
            CandidateTrack(position=i + 1, title=t, duration_ms=200000)
            for i, t in enumerate(tracks)
        ),
    )


def test_primary_identity_coherent() -> None:
    local = [TrackMeta(title="T1", duration_ms=200000), TrackMeta(title="T2", duration_ms=200000)]
    cand = _release("musicbrainz", "Album", ["T1", "T2"])
    res = propose_for_group(local, [cand], album="Album", album_artist="Artist")
    # primary candidate must be single source, not merged
    assert res.ranked[0].candidate.source == "musicbrainz"
    assert res.decision.band in {"strong", "ambiguous"}


def test_rejected_not_strong_ambiguous() -> None:
    local = [TrackMeta(title="Unrelated", duration_ms=100000)]
    cand = _release("deezer", "Wrong", ["Wrong"])
    res = propose_for_group(local, [cand], album="Album", album_artist="Artist")
    assert res.decision.band == "reject" or res.ranked[0].rejected


def test_strong_vs_ambiguous_bands() -> None:
    from muzilla.matching.engine import propose_for_singleton

    strong_cand = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="s"),
        album="A",
        album_artist="Artist",
        year=1999,
        tracks=(CandidateTrack(position=1, title="T1", artist="Artist", duration_ms=200000),),
    )
    res_strong = propose_for_singleton(
        TrackMeta(title="T1", artist="Artist", duration_ms=200000), [strong_cand]
    )
    assert res_strong.decision.strong and res_strong.decision.band == "strong"
    assert not res_strong.decision.rejected
    amb_cand = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="a"),
        album="A",
        album_artist="Artist",
        year=1999,
        tracks=(CandidateTrack(position=1, title="T1x", artist="Artist", duration_ms=200000),),
    )
    res_amb = propose_for_singleton(
        TrackMeta(title="T1", artist="Artist", duration_ms=200000), [amb_cand]
    )
    assert res_amb.decision.band == "ambiguous"
    assert res_amb.decision.ambiguous and not res_amb.decision.rejected
    assert not res_amb.decision.strong


def test_candidate_snapshot_has_provenance_fields() -> None:
    from muzilla.jobs.handlers.match import _candidate_snapshot
    from muzilla.matching.candidates import ScoredCandidate
    from muzilla.pipeline.matching import candidate_row

    cand = _release("musicbrainz", "Album", ["T1"])
    scored = ScoredCandidate(candidate=cand, distance=0.05, adjusted_distance=0.05, signals=())
    row = candidate_row(scored)
    # simulate _candidate_snapshot
    snap = _candidate_snapshot(row)
    for field in ["provider", "ref", "type", "title", "thumbnail", "confidence_band", "signals"]:
        assert field in snap


def test_manual_overwrite_requires_confirmation(db_session: Session) -> None:
    """Provider refresh must not silently overwrite manual edits without force."""
    from datetime import UTC, datetime

    from muzilla.db.models import Track
    from muzilla.services.proposals import ProposalComposer

    now = datetime.now(UTC)
    track = Track(
        path="/music/manual.mp3",
        filename="manual.mp3",
        ext=".mp3",
        size_bytes=1,
        mtime_ns=1,
        title="Original Title",
        artist="Original Artist",
        first_seen_at=now,
        last_scanned_at=now,
    )
    db_session.add(track)
    db_session.flush()
    composer = ProposalComposer(db_session)
    # manual edit
    composer.compose_manual_track_edit(track_id=track.id, field_values={"title": "Manual Title"})
    # provider candidate that would overwrite title
    provider_cand = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="prov-1"),
        album="Album",
        album_artist="Artist",
        tracks=(CandidateTrack(position=1, title="Provider Title", artist="Artist"),),
    )
    bundle = composer.open_bundle_for_scope(db_session, scope_type="track", scope_id=track.id)
    assert bundle is not None
    # without force should raise confirmation required
    try:
        composer.compose_candidate(bundle, provider_cand)
        raise AssertionError("should require confirmation")
    except Exception as exc:  # ProposalCompositionError -> ManualSearchError wrapper
        assert "confirmation required" in str(exc).lower()
    # with force should succeed and provider wins
    detail = composer.compose_candidate(bundle, provider_cand, force=True)
    titles = {
        op.field: op.proposed_value
        for op in detail.current_revision.operations
        if op.kind == "set_tag"
    }
    assert titles.get("title") == "Provider Title"
    # provenance for provider field preserved
    title_op = next(op for op in detail.current_revision.operations if op.field == "title")
    assert title_op.provenance.get("provider") == "musicbrainz"
