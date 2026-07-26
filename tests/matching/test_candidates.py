from __future__ import annotations

import pytest

from muzilla.matching.candidates import gather_candidates, rank_candidates
from muzilla.providers.base import ProviderRef, ReleaseCandidate


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
    )
    for sc in ranked:
        assert sc.adjusted_distance < sc.distance


def test_rank_candidates_source_priority_breaks_ties() -> None:
    a = _candidate("deezer")
    b = _candidate("musicbrainz")

    ranked = rank_candidates(
        [a, b],
        score_fn=lambda c: 0.3,  # identical raw distance
        dup_score_fn=lambda x, y: 1.0,
        source_priority=("musicbrainz", "discogs", "deezer"),
        source_penalty=0.02,
    )
    # musicbrainz is earlier in priority -> lower adjusted distance -> ranked first
    assert ranked[0].candidate.source == "musicbrainz"


def test_rank_candidates_empty_input() -> None:
    assert rank_candidates([], score_fn=lambda c: 0.0, dup_score_fn=lambda x, y: 1.0) == []
