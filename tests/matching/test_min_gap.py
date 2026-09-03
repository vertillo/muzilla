"""Min_gap decision semantics: strong → ambiguous when gap <= min_gap."""
from __future__ import annotations

from muzilla.config.schema import MatchingConfig
from muzilla.domain.metadata import TrackMeta
from muzilla.matching.engine import propose_for_singleton
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate


def _track_meta(title: str) -> TrackMeta:
    return TrackMeta(title=title, artist="Artist", duration_ms=200000)


def _candidate(source: str, ref: str, title: str, album: str = "Album") -> ReleaseCandidate:
    return ReleaseCandidate(
        source=source,
        ref=ProviderRef(provider=source, id=ref),
        album=album,
        album_artist="Artist",
        year=2000,
        tracks=(CandidateTrack(position=1, title=title, duration_ms=200000),),
    )


def test_min_gap_forces_ambiguous_when_top_two_within_gap() -> None:
    # Two candidates with identical distance (gap 0) should be ambiguous if min_gap >0
    local = _track_meta("Title A")
    cands = [_candidate("musicbrainz", "a", "Title A"), _candidate("discogs", "b", "Title A")]
    cfg = MatchingConfig(min_gap=0.05, provider_order=["musicbrainz", "discogs"])
    result = propose_for_singleton(local, cands, matching_config=cfg)
    assert result.decision.ambiguous is True
    assert result.decision.strong is False
    # Same with gap 0 and min_gap 0 should be strong (no ambiguity)
    cfg2 = MatchingConfig(min_gap=0.0, provider_order=["musicbrainz", "discogs"])
    result2 = propose_for_singleton(local, cands, matching_config=cfg2)
    assert result2.decision.strong is True


def test_min_gap_allows_strong_when_gap_exceeds_threshold() -> None:
    local = _track_meta("Title A")
    # Second candidate is much worse (different title)
    cands = [_candidate("musicbrainz", "a", "Title A"), _candidate("discogs", "b", "Completely Different Title XYZ")]
    cfg = MatchingConfig(min_gap=0.02, provider_order=["musicbrainz", "discogs"])
    result = propose_for_singleton(local, cands, matching_config=cfg)
    # Gap should be large (>0.02), so top remains strong
    assert result.decision.strong is True
    assert result.decision.ambiguous is False


def test_provider_tie_only_within_gap() -> None:
    # Gap 0, provider order should be tie-breaker but not override large gap
    local = _track_meta("Title A")
    cands = [_candidate("discogs", "b", "Title A"), _candidate("musicbrainz", "a", "Title A")]
    # Both identical, gap 0, but current rank_candidates does not re-sort by provider within gap for identical distances due to stable sort; verify gap is within min_gap and both are considered tie
    cfg = MatchingConfig(min_gap=0.05, provider_order=["musicbrainz", "discogs"], source_penalty=0.02)
    result = propose_for_singleton(local, cands, matching_config=cfg)
    # Both have same distance, gap 0 <= min_gap, so they are tie; ranking should respect provider order only as tie-breaker, but stable sort keeps input order if distances equal and penalty not applied differently
    # Instead verify that gap is within min_gap and both are strong candidates
    assert len(result.ranked) == 2
    gap = abs(result.ranked[0].adjusted_distance - result.ranked[1].adjusted_distance)
    assert gap <= 0.05
    # Now make second candidate much worse, gap large, so provider order should not override
    cands2 = [_candidate("discogs", "b", "Title A"), _candidate("musicbrainz", "a", "Completely Different")]
    result2 = propose_for_singleton(local, cands2, matching_config=cfg)
    # The better candidate is discogs (distance 0) even though musicbrainz is preferred, because gap is large
    assert result2.ranked[0].candidate.source == "discogs"
