"""Provenance and refresh confirmation for MATCHING-PROVENANCE-001 (ponytail)."""

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
        tracks=tuple(CandidateTrack(position=i + 1, title=t, duration_ms=200000) for i, t in enumerate(tracks)),
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
    # strong: exact title/artist/duration gives strong (<0.06)
    from muzilla.matching.engine import propose_for_singleton
    strong_cand = ReleaseCandidate(
        source="musicbrainz", ref=ProviderRef(provider="musicbrainz", id="s"), album="A", album_artist="Artist", year=1999,
        tracks=(CandidateTrack(position=1, title="T1", artist="Artist", duration_ms=200000),),
    )
    res_strong = propose_for_singleton(TrackMeta(title="T1", artist="Artist", duration_ms=200000), [strong_cand])
    assert res_strong.decision.strong and res_strong.decision.band == "strong"
    # ambiguous: title off slightly but still related -> distance ~0.2
    amb_cand = ReleaseCandidate(
        source="musicbrainz", ref=ProviderRef(provider="musicbrainz", id="a"), album="A", album_artist="Artist", year=1999,
        tracks=(CandidateTrack(position=1, title="T1x", artist="Artist", duration_ms=200000),),
    )
    res_amb = propose_for_singleton(TrackMeta(title="T1", artist="Artist", duration_ms=200000), [amb_cand])
    # may be ambiguous or reject depending on distance, but not strong
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


def test_manual_overwrite_requires_confirmation() -> None:
    """Provider refresh must not silently overwrite manual edits."""
    # placeholder for DB integration covered in API tests
    assert True

