from __future__ import annotations

from muzilla.domain.metadata import TrackMeta
from muzilla.matching.engine import propose_for_group, propose_for_singleton
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate


def _release(
    source: str,
    album: str,
    album_artist: str,
    tracks: list[tuple[str, int]],
    year: int | None = None,
    original_year: int | None = None,
    barcode: str | None = None,
    label: str | None = None,
    catalog_number: str | None = None,
) -> ReleaseCandidate:
    return ReleaseCandidate(
        source=source,
        ref=ProviderRef(provider=source, id=f"{source}-release"),
        album=album,
        album_artist=album_artist,
        year=year,
        original_year=original_year,
        barcode=barcode,
        label=label,
        catalog_number=catalog_number,
        tracks=tuple(
            CandidateTrack(position=i + 1, title=title, duration_ms=dur)
            for i, (title, dur) in enumerate(tracks)
        ),
    )


def _local_track(title: str, duration_ms: int, artist: str = "Sigur Rós") -> TrackMeta:
    return TrackMeta(title=title, artist=artist, album_artist=artist, duration_ms=duration_ms)


class TestProposeForGroup:
    def test_exact_match_scores_low_distance_and_auto_applies(self) -> None:
        local = [
            _local_track("Intro", 100_000),
            _local_track("Svefn-g-englar", 200_000),
            _local_track("Starálfur", 180_000),
        ]
        candidate = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Intro", 100_000), ("Svefn-g-englar", 200_000), ("Starálfur", 180_000)],
        )
        result = propose_for_group(
            local, [candidate], album="Ágætis byrjun", album_artist="Sigur Rós"
        )
        assert result.ranked[0].distance < 0.05
        assert result.decision.auto_applicable

    def test_wrong_release_scores_high_distance(self) -> None:
        local = [_local_track("Intro", 100_000)]
        candidate = _release(
            "musicbrainz",
            album="Definitely Maybe",
            album_artist="Oasis",
            tracks=[("Rock 'n' Roll Star", 300_000)],
        )
        result = propose_for_group(
            local, [candidate], album="Ágætis byrjun", album_artist="Sigur Rós"
        )
        assert result.ranked[0].distance > 0.5
        assert not result.decision.auto_applicable

    def test_missing_tracks_increase_distance(self) -> None:
        local = [_local_track("Intro", 100_000)]  # only 1 of 3 tracks present
        candidate = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Intro", 100_000), ("Svefn-g-englar", 200_000), ("Starálfur", 180_000)],
        )
        result_full = propose_for_group(
            [
                _local_track("Intro", 100_000),
                _local_track("Svefn-g-englar", 200_000),
                _local_track("Starálfur", 180_000),
            ],
            [candidate],
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
        )
        result_partial = propose_for_group(
            local, [candidate], album="Ágætis byrjun", album_artist="Sigur Rós"
        )
        assert result_partial.ranked[0].distance > result_full.ranked[0].distance

    def test_out_of_order_tracks_still_align_correctly(self) -> None:
        local = [
            _local_track("Starálfur", 180_000),
            _local_track("Intro", 100_000),
        ]
        candidate = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Intro", 100_000), ("Starálfur", 180_000)],
        )
        result = propose_for_group(
            local, [candidate], album="Ágætis byrjun", album_artist="Sigur Rós"
        )
        alignment = result.alignments[0]
        by_local = {a.local_index: a.candidate_index for a in alignment}
        assert by_local[0] == 1  # local "Starálfur" -> candidate index 1
        assert by_local[1] == 0  # local "Intro" -> candidate index 0

    def test_multiple_candidates_are_ranked_best_first(self) -> None:
        local = [
            _local_track("Intro", 100_000),
            _local_track("Svefn-g-englar", 200_000),
        ]
        good = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Intro", 100_000), ("Svefn-g-englar", 200_000)],
        )
        bad = _release(
            "deezer",
            album="Von",
            album_artist="Sigur Rós",
            tracks=[("Sigur Rós", 300_000)],
        )
        result = propose_for_group(
            local, [bad, good], album="Ágætis byrjun", album_artist="Sigur Rós"
        )
        assert result.ranked[0].candidate.source == "musicbrainz"

    def test_no_candidates_returns_no_auto_apply(self) -> None:
        result = propose_for_group([_local_track("Intro", 100_000)], [])
        assert result.ranked == []
        assert not result.decision.auto_applicable
        assert not result.decision.needs_confirmation


class TestProposeForSingleton:
    def test_exact_recording_match_is_confident(self) -> None:
        local = TrackMeta(title="Starálfur", artist="Sigur Rós", duration_ms=180_000)
        candidate = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Starálfur", 180_000)],
            year=1999,
        )
        result = propose_for_singleton(local, [candidate])
        assert result.ranked[0].distance < 0.06
        assert result.decision.auto_applicable

    def test_singleton_auto_threshold_is_stricter_than_album(self) -> None:
        # A distance that would auto-apply for an album (< 0.10) must NOT
        # auto-apply for a singleton (< 0.06) -- fewer corroborating
        # signals means a confident-looking wrong match is easier here.
        local = TrackMeta(title="Starálfur", artist="Sigur Rós", duration_ms=180_000)
        candidate = _release(
            "musicbrainz",
            album="Ágætis byrjun",
            album_artist="Sigur Rós",
            tracks=[("Staralfur", 190_000)],  # slightly off title/duration
        )
        result = propose_for_singleton(local, [candidate])
        if 0.06 <= result.ranked[0].distance < 0.10:
            assert not result.decision.auto_applicable

    def test_prefer_earliest_release_breaks_near_ties(self) -> None:
        local = TrackMeta(title="Wonderwall", artist="Oasis", duration_ms=258_000)
        original = _release(
            "musicbrainz",
            album="(What's the Story) Morning Glory?",
            album_artist="Oasis",
            tracks=[("Wonderwall", 258_000)],
            year=1995,
            original_year=1995,
        )
        compilation = _release(
            "discogs",
            album="Stop the Clocks",
            album_artist="Oasis",
            tracks=[("Wonderwall", 258_000)],
            year=2006,
            original_year=2006,
        )
        result = propose_for_singleton(
            local, [compilation, original], prefer_earliest_release=True
        )
        assert result.ranked[0].candidate.album == "(What's the Story) Morning Glory?"

    def test_prefer_earliest_release_disabled_keeps_pure_distance_order(self) -> None:
        local = TrackMeta(title="Wonderwall", artist="Oasis", duration_ms=258_000)
        original = _release(
            "musicbrainz",
            album="(What's the Story) Morning Glory?",
            album_artist="Oasis",
            tracks=[("Wonderwall", 258_000)],
            year=1995,
        )
        result = propose_for_singleton(local, [original], prefer_earliest_release=False)
        assert result.ranked[0].candidate.source == "musicbrainz"

    def test_no_candidates_returns_no_auto_apply(self) -> None:
        result = propose_for_singleton(TrackMeta(title="X", artist="Y"), [])
        assert result.ranked == []
        assert not result.decision.auto_applicable
