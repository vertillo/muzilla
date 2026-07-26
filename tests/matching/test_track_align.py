from __future__ import annotations

from muzilla.domain.normalize import string_dist
from muzilla.matching.track_align import align_tracks


def test_align_tracks_perfect_match_in_order() -> None:
    local = ["Intro", "Song A", "Song B"]
    candidate = ["Intro", "Song A", "Song B"]
    result = align_tracks(local, candidate, string_dist)
    assert len(result) == 3
    for a in result:
        assert a.local_index == a.candidate_index
        assert a.cost < 0.05


def test_align_tracks_out_of_order_still_matches_by_content() -> None:
    local = ["Song B", "Song A"]
    candidate = ["Song A", "Song B"]
    result = align_tracks(local, candidate, string_dist)
    by_local = {a.local_index: a.candidate_index for a in result}
    assert by_local[0] == 1  # "Song B" -> candidate index 1
    assert by_local[1] == 0  # "Song A" -> candidate index 0


def test_align_tracks_missing_candidate_track_is_unmatched() -> None:
    local = ["Song A", "Bonus Track"]
    candidate = ["Song A"]
    result = align_tracks(local, candidate, string_dist)
    assert len(result) == 2
    matched = [a for a in result if a.candidate_index is not None]
    unmatched = [a for a in result if a.candidate_index is None]
    assert len(matched) == 1
    assert len(unmatched) == 1
    assert unmatched[0].local_index == 1


def test_align_tracks_extra_candidate_track_is_unmatched() -> None:
    local = ["Song A"]
    candidate = ["Song A", "Hidden Track"]
    result = align_tracks(local, candidate, string_dist)
    unmatched = [a for a in result if a.local_index is None]
    assert len(unmatched) == 1
    assert unmatched[0].candidate_index == 1


def test_align_tracks_empty_both_sides() -> None:
    assert align_tracks([], [], string_dist) == []


def test_align_tracks_sequential_prior_breaks_ties() -> None:
    # Two candidate tracks equally distant textually from a local track;
    # the sequential prior should favor the one at the matching index.
    local = ["Track", "Other"]
    candidate = ["Other", "Track"]

    def always_equal(_a: object, _b: object) -> float:
        return 0.5

    result = align_tracks(local, candidate, always_equal)
    by_local = {a.local_index: a.candidate_index for a in result}
    # With identical base cost, natural order (0->0, 1->1) is cheaper
    # than the swap under the sequential prior.
    assert by_local[0] == 0
    assert by_local[1] == 1
