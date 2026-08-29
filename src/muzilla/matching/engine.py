"""The matching engine's two entry points.

Album/release matching and singleton/recording matching are different
problems, not one code path with a flag: a release has a tracklist to
align and corroborating signals (track count, media, label) a lone
recording never has. `propose_for_group` and `propose_for_singleton`
are kept as two separate, equally-weighted functions rather than one
that branches internally — matching a 50/50 flat library means neither
path is the "normal" one the other is a special case of.

Pure with respect to I/O: takes already-fetched `ReleaseCandidate`s
(from `matching/candidates.py`'s `gather_candidates`) and local
`TrackMeta`s, returns ranked proposals. No network, no DB — callers in
`services/` own fetching and persistence.
"""

from __future__ import annotations

from dataclasses import dataclass

from muzilla.domain.metadata import TrackMeta
from muzilla.domain.normalize import string_dist
from muzilla.matching.candidates import CandidateScore, ScoredCandidate, rank_candidates
from muzilla.matching.distance import (
    exact_distance,
    explained_weighted_distance,
    numeric_distance,
    weighted_distance,
)
from muzilla.matching.track_align import TrackAlignment, align_tracks
from muzilla.matching.weights import (
    ALBUM_REJECT_THRESHOLD,
    ALBUM_STRONG_THRESHOLD,
    ALBUM_WEIGHTS,
    DEFAULT_SOURCE_PENALTY,
    DEFAULT_SOURCE_PRIORITY,
    SINGLETON_REJECT_THRESHOLD,
    SINGLETON_STRONG_THRESHOLD,
    SINGLETON_WEIGHTS,
    TRACK_WEIGHTS,
)
from muzilla.providers.base import CandidateTrack, ReleaseCandidate

# Legacy aliases for compatibility (weights keeps them too); new code uses STRONG/REJECT.
ALBUM_AUTO_THRESHOLD = ALBUM_STRONG_THRESHOLD
ALBUM_CONFIRM_THRESHOLD = ALBUM_REJECT_THRESHOLD
SINGLETON_AUTO_THRESHOLD = SINGLETON_STRONG_THRESHOLD
_SINGLETON_CONFIRM_THRESHOLD = SINGLETON_REJECT_THRESHOLD

# Tolerances for numeric distance saturation.
_YEAR_SCALE = 2.0
_DURATION_SCALE_MS = 10_000.0


@dataclass(frozen=True, slots=True)
class MatchDecision:
    strong: bool
    """distance < STRONG_THRESHOLD — strong band; may preselect, never auto-apply."""
    ambiguous: bool
    """STRONG <= distance < REJECT and related — ambiguous band; requires explicit selection or Skip."""
    rejected: bool = False
    rejection_reason: str | None = None

    # Legacy aliases — keep tests and old callers working while new code uses strong/ambiguous.
    @property
    def auto_applicable(self) -> bool:  # pragma: no cover
        return self.strong

    @property
    def needs_confirmation(self) -> bool:  # pragma: no cover
        return self.ambiguous

    @property
    def band(self) -> str:
        if self.rejected:
            return "reject"
        if self.strong:
            return "strong"
        if self.ambiguous:
            return "ambiguous"
        return "reject"


def _decide(
    distance: float,
    strong: float,
    reject: float,
    *,
    rejected: bool = False,
    rejection_reason: str | None = None,
) -> MatchDecision:
    is_strong = not rejected and distance < strong
    is_ambiguous = not rejected and not is_strong
    # rejected stays rejected regardless of distance; ambiguous covers every other selectable
    # ponytail: no separate ambiguous threshold — ambiguous is any selectable not strong, bounded by reject (0.45 via candidates)
    return MatchDecision(
        strong=is_strong,
        ambiguous=is_ambiguous and not rejected,
        rejected=rejected,
        rejection_reason=rejection_reason,
    )


def _decision_for_ranked(
    ranked: list[ScoredCandidate], strong: float, reject: float
) -> MatchDecision:
    """Describe the first candidate that remains available to callers.

    Rejected rows stay in ``ranked`` for scoring diagnostics, but the pipeline
    omits them from proposals. A proposal-level decision must therefore not
    report the rejection of a hidden row when a later candidate is selectable.
    """
    selected = next((candidate for candidate in ranked if not candidate.rejected), None)
    if selected is None:
        selected = ranked[0] if ranked else None
    if selected is None:
        return MatchDecision(strong=False, ambiguous=False)
    return _decide(
        selected.adjusted_distance,
        strong,
        reject,
        rejected=selected.rejected,
        rejection_reason=selected.rejection_reason,
    )


@dataclass(frozen=True, slots=True)
class AlbumMatchResult:
    ranked: list[ScoredCandidate]
    alignments: dict[int, list[TrackAlignment]]
    """Candidate index (into `ranked`) -> per-track Hungarian alignment
    against the local group's tracks."""
    decision: MatchDecision
    """Computed against the top non-rejected candidate, if any."""


@dataclass(frozen=True, slots=True)
class SingletonMatchResult:
    ranked: list[ScoredCandidate]
    decision: MatchDecision


def track_pair_distance(local: TrackMeta, candidate: CandidateTrack) -> float:
    # Per-track candidate artist/isrc/mb_track_id are frequently absent
    # (MusicBrainz's release-level track listing often carries only the
    # release's overall artist credit) -- omit those keys entirely
    # rather than scoring them as a mismatch, so weighted_distance
    # excludes them from the denominator instead of treating "no data"
    # as "definitely wrong" (see distance.py's weighted_distance
    # docstring: missing fields must not count against a match).
    field_dists: dict[str, float] = {"index": 0.0}
    if local.title and candidate.title:
        field_dists["title"] = string_dist(local.title, candidate.title)
    if local.duration_ms is not None and candidate.duration_ms is not None:
        field_dists["length"] = numeric_distance(
            float(local.duration_ms), float(candidate.duration_ms), scale=_DURATION_SCALE_MS
        )
    if local.artist and candidate.artist:
        field_dists["artist"] = string_dist(local.artist, candidate.artist)
    if local.mb_track_id and candidate.mb_track_id:
        field_dists["track_id"] = exact_distance(local.mb_track_id, candidate.mb_track_id)
    if local.isrc and candidate.isrc:
        field_dists["isrc"] = exact_distance(local.isrc, candidate.isrc)
    return weighted_distance(field_dists, TRACK_WEIGHTS)


def _album_candidate_score(
    local_tracks: list[TrackMeta],
    local_album: str | None,
    local_album_artist: str | None,
    local_year: int | None,
    local_label: str | None,
    local_catalog_number: str | None,
    local_country: str | None,
    local_media: str | None,
    local_barcode: str | None,
    candidate: ReleaseCandidate,
) -> tuple[CandidateScore, list[TrackAlignment]]:
    alignment = align_tracks(local_tracks, list(candidate.tracks), track_pair_distance)

    n_local, n_cand = len(local_tracks), len(candidate.tracks)
    matched = [a for a in alignment if a.local_index is not None and a.candidate_index is not None]
    missing = max(0, n_cand - len(matched))
    unmatched = max(0, n_local - len(matched))
    tracks_dist = sum(a.cost for a in matched) / len(matched) if matched else 1.0

    # Fields with no local data (a track scanned with sparse tags has
    # no barcode/catalog_number/media/country/label at all) are omitted
    # entirely rather than scored as a mismatch — weighted_distance
    # only excludes a field from its denominator when the key is
    # missing, so "local has no barcode" must not silently become
    # "definitely the wrong barcode."
    field_dists: dict[str, float] = {
        "tracks": tracks_dist,
        "missing_tracks": min(missing / max(n_cand, 1), 1.0),
        "unmatched_tracks": min(unmatched / max(n_local, 1), 1.0),
    }
    if local_album and candidate.album:
        field_dists["album"] = string_dist(local_album, candidate.album)
    if local_album_artist and candidate.album_artist:
        field_dists["album_artist"] = string_dist(local_album_artist, candidate.album_artist)
    if local_year is not None and candidate.year is not None:
        field_dists["year"] = numeric_distance(
            float(local_year), float(candidate.year), scale=_YEAR_SCALE
        )
    if local_media and candidate.media:
        field_dists["media"] = exact_distance(local_media, candidate.media)
    if local_country and candidate.country:
        field_dists["country"] = exact_distance(local_country, candidate.country)
    if local_label and candidate.label:
        field_dists["label"] = string_dist(local_label, candidate.label)
    if local_catalog_number and candidate.catalog_number:
        field_dists["catalog_number"] = exact_distance(
            local_catalog_number, candidate.catalog_number
        )
    if local_barcode and candidate.barcode:
        field_dists["barcode"] = exact_distance(local_barcode, candidate.barcode)
    distance, signals = explained_weighted_distance(field_dists, ALBUM_WEIGHTS)
    album_related = (
        local_album is not None
        and candidate.album is not None
        and string_dist(local_album, candidate.album) < 0.45
    )
    track_related = any(alignment_item.cost < 0.45 for alignment_item in matched)
    return CandidateScore(
        distance=distance, signals=signals, related=album_related or track_related
    ), alignment


def propose_for_group(
    local_tracks: list[TrackMeta],
    candidates: list[ReleaseCandidate],
    *,
    album: str | None = None,
    album_artist: str | None = None,
    year: int | None = None,
    label: str | None = None,
    catalog_number: str | None = None,
    country: str | None = None,
    media: str | None = None,
    barcode: str | None = None,
    source_priority: tuple[str, ...] = DEFAULT_SOURCE_PRIORITY,
    source_penalty: float = DEFAULT_SOURCE_PENALTY,
) -> AlbumMatchResult:
    """Release-level matching: score every candidate against the local
    group using ALBUM_WEIGHTS + Hungarian track alignment, rank with
    duplicate flagging and corroboration, and compute the top match's
    auto-apply/confirm decision.
    """
    # Alignment is computed once per candidate and cached by identity
    # (each ReleaseCandidate instance is only scored once per call, and
    # the dataclass isn't hashable-by-value here) so rank_candidates'
    # score_fn and the final alignments-by-rank pass never redo the
    # same O(n^3) Hungarian solve twice.
    by_id: dict[int, tuple[CandidateScore, list[TrackAlignment]]] = {}

    def _score_and_alignment(c: ReleaseCandidate) -> tuple[CandidateScore, list[TrackAlignment]]:
        key = id(c)
        if key not in by_id:
            by_id[key] = _album_candidate_score(
                local_tracks,
                album,
                album_artist,
                year,
                label,
                catalog_number,
                country,
                media,
                barcode,
                c,
            )
        return by_id[key]

    def score_fn(c: ReleaseCandidate) -> CandidateScore:
        return _score_and_alignment(c)[0]

    def dup_score_fn(a: ReleaseCandidate, b: ReleaseCandidate) -> float:
        return string_dist(a.album, b.album)

    ranked = rank_candidates(
        candidates,
        score_fn,
        dup_score_fn,
        source_priority=source_priority,
        source_penalty=source_penalty,
    )

    alignments: dict[int, list[TrackAlignment]] = {
        i: _score_and_alignment(sc.candidate)[1] for i, sc in enumerate(ranked)
    }

    decision = _decision_for_ranked(ranked, ALBUM_STRONG_THRESHOLD, ALBUM_REJECT_THRESHOLD)
    return AlbumMatchResult(ranked=ranked, alignments=alignments, decision=decision)


def _singleton_candidate_score(local: TrackMeta, candidate: ReleaseCandidate) -> CandidateScore:
    """Recording-level distance: title + artist + duration + ISRC +
    fingerprint (acoustid handled by the caller pre-filtering/boosting
    candidates it already fingerprint-matched — this function scores
    the textual/numeric signal only).

    `candidate` here represents one recording credited to one release
    (the release the singleton would be tagged with if picked) — its
    single relevant track is whichever of `candidate.tracks` matches
    best, since a singleton query still returns full-release
    candidates from providers that don't have a recording-only search.
    """
    best_track = min(
        candidate.tracks,
        key=lambda t: track_pair_distance(local, t),
        default=None,
    )
    track_title = best_track.title if best_track else candidate.album
    track_artist = best_track.artist if best_track and best_track.artist else candidate.album_artist
    track_duration = best_track.duration_ms if best_track else None
    track_isrc = best_track.isrc if best_track else None

    # Same "omit rather than penalize missing data" rule as
    # _album_candidate_distance/track_pair_distance above.
    field_dists: dict[str, float] = {}
    if local.title and track_title:
        field_dists["title"] = string_dist(local.title, track_title)
    if local.artist and track_artist:
        field_dists["artist"] = string_dist(local.artist, track_artist)
    if local.duration_ms is not None and track_duration is not None:
        field_dists["length"] = numeric_distance(
            float(local.duration_ms), float(track_duration), scale=_DURATION_SCALE_MS
        )
    if local.isrc and track_isrc:
        field_dists["isrc"] = exact_distance(local.isrc, track_isrc)
    acoustid_ext = candidate.external_ids.get("acoustid")
    if local.acoustid_id and acoustid_ext:
        field_dists["acoustid"] = exact_distance(local.acoustid_id, acoustid_ext)
    distance, signals = explained_weighted_distance(field_dists, SINGLETON_WEIGHTS)
    exact_id = any(
        field_dists.get(field) == 0.0 for field in ("isrc", "acoustid") if field in field_dists
    )
    title_related = field_dists.get("title", 1.0) < 0.45
    related = exact_id or title_related
    return CandidateScore(
        distance=distance,
        signals=signals,
        representative_track=best_track,
        related=related,
    )


def propose_for_singleton(
    local: TrackMeta,
    candidates: list[ReleaseCandidate],
    *,
    source_priority: tuple[str, ...] = DEFAULT_SOURCE_PRIORITY,
    source_penalty: float = DEFAULT_SOURCE_PENALTY,
    prefer_earliest_release: bool = True,
) -> SingletonMatchResult:
    """Recording-level matching for loose tracks.

    Fewer corroborating signals than album matching (no tracklist to
    align, no track-count/media corroboration), so a confident-looking
    wrong match is easier to produce — the auto-apply threshold is
    stricter (0.06 vs album's 0.10).

    A singleton recording can appear on the original album, several
    compilations, and a deluxe reissue; `prefer_earliest_release`
    (the default) breaks near-ties
    toward the earliest `original_year`/`year` so a single doesn't get
    silently credited to "Now That's What I Call Music 47".
    """

    def score_fn(c: ReleaseCandidate) -> CandidateScore:
        return _singleton_candidate_score(local, c)

    def dup_score_fn(a: ReleaseCandidate, b: ReleaseCandidate) -> float:
        return string_dist(a.album, b.album)

    ranked = rank_candidates(
        candidates,
        score_fn,
        dup_score_fn,
        source_priority=source_priority,
        source_penalty=source_penalty,
    )

    if prefer_earliest_release and ranked:
        # Stable re-sort: among candidates within a tight distance band
        # of the best score, prefer the earliest release rather than
        # whichever provider happened to answer first / rank highest.
        best = ranked[0].adjusted_distance
        band = 0.02

        def sort_key(sc: ScoredCandidate) -> tuple[float, int]:
            in_band = sc.adjusted_distance <= best + band
            year = sc.candidate.original_year or sc.candidate.year
            return (sc.adjusted_distance, (year if in_band and year is not None else 9999))

        ranked = sorted(ranked, key=sort_key)

    decision = _decision_for_ranked(ranked, SINGLETON_STRONG_THRESHOLD, SINGLETON_REJECT_THRESHOLD)
    return SingletonMatchResult(ranked=ranked, decision=decision)
