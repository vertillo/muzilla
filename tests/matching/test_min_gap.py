"""Min_gap decision semantics: strong → ambiguous when gap <= min_gap."""

from __future__ import annotations

from dataclasses import replace

from muzilla.config.schema import MatchingConfig
from muzilla.domain.metadata import TrackMeta
from muzilla.matching.candidates import ScoredCandidate
from muzilla.matching.engine import _decision_for_ranked, propose_for_singleton
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


def test_min_gap_uses_absolute_score_difference_when_order_is_reversed() -> None:
    preferred = ScoredCandidate(
        candidate=_candidate("musicbrainz", "preferred", "Title A"),
        distance=0.05,
        adjusted_distance=0.05,
    )
    better = ScoredCandidate(
        candidate=_candidate("discogs", "better", "Title A"),
        distance=0.01,
        adjusted_distance=0.01,
    )

    decision = _decision_for_ranked([preferred, better], strong=0.06, reject=0.45, min_gap=0.03)

    assert decision.strong is True
    assert decision.ambiguous is False


def test_min_gap_allows_strong_when_gap_exceeds_threshold() -> None:
    local = _track_meta("Title A")
    # Second candidate is much worse (different title)
    cands = [
        _candidate("musicbrainz", "a", "Title A"),
        _candidate("discogs", "b", "Completely Different Title XYZ"),
    ]
    cfg = MatchingConfig(min_gap=0.02, provider_order=["musicbrainz", "discogs"])
    result = propose_for_singleton(local, cands, matching_config=cfg)
    # Gap should be large (>0.02), so top remains strong
    assert result.decision.strong is True
    assert result.decision.ambiguous is False


def test_provider_tie_only_within_gap() -> None:
    # Gap 0, provider order should be tie-breaker but not override large gap
    local = _track_meta("Title A")
    cands = [_candidate("discogs", "b", "Title A"), _candidate("musicbrainz", "a", "Title A")]
    # Exact scores let provider order break the tie without changing match quality.
    cfg = MatchingConfig(
        min_gap=0.05, provider_order=["musicbrainz", "discogs"], source_penalty=0.02
    )
    result = propose_for_singleton(local, cands, matching_config=cfg)
    assert len(result.ranked) == 2
    assert result.ranked[0].candidate.source == "musicbrainz"
    assert result.ranked[0].adjusted_distance == result.ranked[1].adjusted_distance
    assert result.decision.ambiguous is True
    # Now make second candidate much worse, gap large, so provider order should not override
    cands2 = [
        _candidate("discogs", "b", "Title A"),
        _candidate("musicbrainz", "a", "Completely Different"),
    ]
    result2 = propose_for_singleton(local, cands2, matching_config=cfg)
    # The better candidate is discogs (distance 0) even though musicbrainz is preferred, because gap is large
    assert result2.ranked[0].candidate.source == "discogs"


def test_singleton_provider_order_survives_earliest_release_sort() -> None:
    local = _track_meta("A" * 50)
    preferred_but_worse = _candidate("musicbrainz", "mb", "B" + "A" * 49, album="MB release")
    better = _candidate("deezer", "deezer", "A" * 50, album="Deezer release")
    config = MatchingConfig(
        min_gap=0.03, provider_order=["musicbrainz", "deezer"], source_penalty=0.04
    )

    result = propose_for_singleton(local, [better, preferred_but_worse], matching_config=config)

    assert result.ranked[0].candidate.source == "musicbrainz"
    assert result.ranked[0].adjusted_distance > result.ranked[1].adjusted_distance
    assert result.ranked[0].adjusted_distance - result.ranked[1].adjusted_distance <= 0.03


def test_singleton_earliest_release_sort_reindexes_duplicate_indices() -> None:
    local = _track_meta("Title A")
    musicbrainz = replace(_candidate("musicbrainz", "mb", "Title A"), original_year=1990)
    deezer = replace(_candidate("deezer", "deezer", "Title A"), original_year=2000)
    config = MatchingConfig(source_penalty=0.0)

    result = propose_for_singleton(local, [deezer, musicbrainz], matching_config=config)

    assert result.ranked[0].candidate.source == "musicbrainz"
    assert result.ranked[0].is_duplicate_of == (1,)
    assert result.ranked[1].candidate.source == "deezer"
    assert result.ranked[1].is_duplicate_of == (0,)
