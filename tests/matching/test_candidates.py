from __future__ import annotations

from dataclasses import replace

import pytest

from muzilla.matching.candidates import gather_candidates, rank_candidates, retrieve_and_hydrate
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate


def _candidate(source: str, album: str = "Abbey Road", barcode: str | None = None, year: int | None = None, n_tracks: int = 0) -> ReleaseCandidate:
    return ReleaseCandidate(
        source=source,
        ref=ProviderRef(provider=source, id=f"{source}-1"),
        album=album,
        album_artist="The Beatles",
        barcode=barcode,
        year=year,
        tracks=tuple(),
    )


@pytest.mark.asyncio
async def test_gather_candidates_merges_all_providers() -> None:
    async def mb_search(q: object, limit: int) -> list[ReleaseCandidate]:
        return [_candidate("musicbrainz")]

    async def deezer_search(q: object, limit: int) -> list[ReleaseCandidate]:
        return [_candidate("deezer")]

    result = await gather_candidates(
        query=None,  # type: ignore[arg-type]
        searchers={"musicbrainz": mb_search, "deezer": deezer_search},
    )
    assert {c.source for c in result} == {"musicbrainz", "deezer"}


@pytest.mark.asyncio
async def test_gather_candidates_survives_one_provider_failing() -> None:
    async def ok_search(q: object, limit: int) -> list[ReleaseCandidate]:
        return [_candidate("musicbrainz")]

    async def broken_search(q: object, limit: int) -> list[ReleaseCandidate]:
        raise RuntimeError("provider is down")

    result = await gather_candidates(
        query=None,  # type: ignore[arg-type]
        searchers={"musicbrainz": ok_search, "discogs": broken_search},
    )
    assert len(result) == 1
    assert result[0].source == "musicbrainz"


def test_rank_candidates_sorts_by_distance() -> None:
    a = _candidate("musicbrainz")
    b = _candidate("deezer")
    distances = {id(a): 0.5, id(b): 0.1}

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: distances[id(c)],
        dup_score_fn=lambda x, y: 1.0,
    )
    assert ranked[0].candidate.source == "deezer"
    assert ranked[1].candidate.source == "musicbrainz"


def test_rank_candidates_flags_shared_barcode_as_duplicate() -> None:
    a = _candidate("musicbrainz", barcode="123456")
    b = _candidate("discogs", barcode="123456")

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: 0.2,
        dup_score_fn=lambda x, y: 1.0,  # would NOT flag by text distance alone
    )
    by_source = {sc.candidate.source: sc for sc in ranked}
    assert by_source["musicbrainz"].is_duplicate_of != ()
    assert by_source["discogs"].is_duplicate_of != ()


def test_rank_candidates_does_not_flag_different_sources_as_duplicate_by_default() -> None:
    a = _candidate("musicbrainz", album="Abbey Road", year=1969)
    b = _candidate("deezer", album="Definitely Maybe", year=1994)

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: 0.2,
        dup_score_fn=lambda x, y: 1.0,
    )
    assert all(sc.is_duplicate_of == () for sc in ranked)


def test_rank_candidates_corroboration_lowers_adjusted_distance() -> None:
    a = _candidate("musicbrainz", barcode="123456")
    b = _candidate("discogs", barcode="123456")

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: 0.5,
        dup_score_fn=lambda x, y: 1.0,
        corroboration_bonus=0.05,
        source_priority=("musicbrainz", "discogs"),
        source_penalty=0.02,
    )
    by_source = {sc.candidate.source: sc for sc in ranked}
    for sc in ranked:
        assert sc.adjusted_distance == pytest.approx(0.45)
        assert sc.adjusted_distance < sc.distance
    assert ranked[0].candidate.source == "musicbrainz"
    assert any(signal.field == "source_priority" for signal in by_source["discogs"].signals)


def test_rank_candidates_source_priority_breaks_ties() -> None:
    a = _candidate("deezer")
    b = _candidate("musicbrainz")

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: 0.3,  # identical raw distance
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "discogs", "deezer"),
        source_penalty=0.02,
        min_gap=0.0,
    )
    # Provider preference changes order without changing the quality score.
    assert ranked[0].candidate.source == "musicbrainz"
    assert [sc.adjusted_distance for sc in ranked] == pytest.approx([0.3, 0.3])


@pytest.mark.parametrize(("min_gap", "worse_distance"), [(0.0, 0.005), (0.03, 0.035)])
def test_rank_candidates_source_priority_cannot_override_outside_min_gap(
    min_gap: float, worse_distance: float
) -> None:
    better = _candidate("deezer")
    worse = _candidate("musicbrainz")
    distances = {id(better): 0.0, id(worse): worse_distance}

    ranked = rank_candidates(
        [better, worse],
        score_fn=lambda c: distances[id(c)],
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "deezer"),
        source_penalty=0.04,
        min_gap=min_gap,
    )

    assert ranked[0].candidate is better


def test_rank_candidates_source_priority_includes_boundary_but_not_outside() -> None:
    better = _candidate("deezer")
    boundary = _candidate("musicbrainz")
    outside = _candidate("discogs")
    distances = {id(better): 0.0, id(boundary): 0.03, id(outside): 0.030000002}

    ranked = rank_candidates(
        [better, boundary, outside],
        score_fn=lambda c: distances[id(c)],
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "deezer", "discogs"),
        source_penalty=0.04,
        min_gap=0.03,
    )

    assert [sc.candidate for sc in ranked] == [boundary, better, outside]
    assert [sc.adjusted_distance for sc in ranked] == pytest.approx([0.03, 0.0, 0.030000002])


def test_rank_candidates_provider_not_in_priority_order_is_unpenalized() -> None:
    unlisted = _candidate("deezer")
    listed = _candidate("discogs")
    distances = {id(unlisted): 0.01, id(listed): 0.0}

    ranked = rank_candidates(
        [unlisted, listed],
        score_fn=lambda c: distances[id(c)],
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "discogs"),
        source_penalty=0.04,
        min_gap=0.03,
    )
    by_source = {sc.candidate.source: sc for sc in ranked}

    assert ranked[0].candidate is unlisted
    assert by_source["deezer"].adjusted_distance == pytest.approx(0.01)
    assert by_source["discogs"].adjusted_distance == pytest.approx(0.0)
    assert not any(signal.field == "source_priority" for signal in by_source["deezer"].signals)


def test_rank_candidates_source_preference_does_not_change_rejection() -> None:
    ranked = rank_candidates(
        [_candidate("discogs")],
        score_fn=lambda c: 0.44,
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "discogs"),
        source_penalty=0.04,
        min_gap=0.03,
        reject_threshold=0.45,
    )

    assert ranked[0].adjusted_distance == pytest.approx(0.44)
    assert ranked[0].rejected is False


def test_rank_candidates_empty_input() -> None:
    assert rank_candidates([], score_fn=lambda c: 0.0, dup_score_fn=lambda x, y: 1.0) == []


@pytest.mark.asyncio
async def test_retrieve_hydrates_shortlist_and_keeps_failed_provider_distinct() -> None:
    summary = _candidate("musicbrainz")
    hydrated = replace(
        summary,
        track_count=1,
        tracks=(CandidateTrack(position=1, title="Come Together", duration_ms=259000),),
    )

    class WorkingProvider:
        async def search_releases(self, query: object, limit: int) -> list[ReleaseCandidate]:
            return [summary, summary]  # duplicate summary must hydrate once

        async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
            return hydrated

    class FailedProvider:
        async def search_releases(self, query: object, limit: int) -> list[ReleaseCandidate]:
            raise RuntimeError("offline")

        async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
            raise AssertionError("must not hydrate a failed search")

    result = await retrieve_and_hydrate(
        query=None,  # type: ignore[arg-type]
        providers={"musicbrainz": WorkingProvider(), "deezer": FailedProvider()},  # type: ignore[dict-item]
        search_limit=12,
        hydrate_limit=4,
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].tracks[0].title == "Come Together"
    assert result.candidates[0].track_count == 1
    assert [(outcome.provider, outcome.status) for outcome in result.provider_outcomes] == [
        ("musicbrainz", "results"),
        ("deezer", "failed"),
    ]
