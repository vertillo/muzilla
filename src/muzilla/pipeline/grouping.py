"""The grouping cascade (docs/PLAN.md §7b) — confidence-scored, because
in a flat library there is no directory signal and muzilla is always
guessing. Every group carries a `grouping_basis` explaining *why*.

Every stage is now implemented:

- Stage 1 — strong identifiers (mb_release_id, barcode,
  (catalog_number, label)). confidence=1.0.
- Stage 2 (Phase 3) — fingerprint consensus: any release MBID shared
  by >=3 files, or by >=50% of a candidate group, forms a group with
  confidence=0.9. The workhorse for the badly-tagged era of the
  collection and the main reason fingerprinting moved to Phase 3 — a
  flat folder with no directory signal often has fingerprints as the
  *only* trustworthy identifier. Runs against persisted
  `TrackFingerprintMatch` rows (Phase 4's fingerprint job populates
  them; this stage is a pure read, it never calls AcoustID itself).
- Stage 3 — fuzzy tag clustering on (album_artist, album) via
  domain.normalize.string_dist. confidence scaled 0.5-0.85 by
  intra-cluster tightness.
- Stage 4 — singleton classification for whatever survives 1-3 as a
  cluster of one, or has no usable album tag, or album==title, or
  track_total==1.

Partial-album detection: a Stage-1/2/3 cluster matching a release of N
tracks (expected_track_count) but holding only M < N is flagged
kind="partial_album" rather than guessed one way or the other — see
docs/PLAN.md's explicit statement that muzilla cannot distinguish
"incomplete rip" from "I only wanted these songs."

**Pinned groups are never touched.** `TrackGroup.is_pinned` means a
user corrected this grouping via the correction UI (services/
grouping.py's merge/split/reassign/pin, all routed through changes/ as
ChangeSets) — rescans must never re-guess something already fixed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import blake2b

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackFingerprintMatch, TrackGroup
from muzilla.domain.normalize import normalize_for_match, string_dist

# Stage 2 fingerprint-consensus thresholds (docs/PLAN.md §7b): a
# release MBID needs either an absolute floor of corroborating tracks
# or a majority of the candidate group, whichever is more permissive
# for small groups (a 2-track EP shouldn't need 3 absolute matches).
_FINGERPRINT_MIN_ABSOLUTE = 3
_FINGERPRINT_MIN_FRACTION = 0.5

# Stage 3 cluster threshold: two tracks' (album_artist, album) pair are
# considered the same release if their combined string_dist is below
# this. Chosen loosely enough to catch "Beatles"/"The Beatles" and
# "Abbey Road"/"Abbey Road (Remastered)" while not merging genuinely
# different albums by the same artist.
_TAG_CLUSTER_THRESHOLD = 0.25


@dataclass(frozen=True, slots=True)
class GroupProposal:
    key: str
    kind: str
    """album | singleton | partial_album | unknown"""
    grouping_basis: str
    """release_id | barcode | tags | singleton"""
    grouping_confidence: float
    track_ids: tuple[int, ...]
    album: str | None = None
    album_artist: str | None = None
    year: int | None = None
    label: str | None = None
    catalog_number: str | None = None
    barcode: str | None = None
    mb_release_id: str | None = None
    expected_track_count: int | None = None


@dataclass(frozen=True, slots=True)
class GroupingRunResult:
    proposals: tuple[GroupProposal, ...]
    groups_created: int = 0
    groups_updated: int = 0
    tracks_grouped: int = 0
    tracks_skipped_pinned: int = 0


def _group_key(basis: str, discriminator: str) -> str:
    return blake2b(f"{basis}:{discriminator}".encode()).hexdigest()[:32]


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _is_singleton_track(track: Track) -> bool:
    """Stage 4 classification for a single track considered in
    isolation (before/after clustering)."""
    if _is_blank(track.album):
        return True
    if track.title and track.album and normalize_for_match(track.album) == normalize_for_match(track.title):
        return True
    return bool(track.track_total == 1)


def _stage1_strong_identifiers(tracks: list[Track]) -> tuple[list[GroupProposal], list[Track]]:
    """Group by mb_release_id, then barcode, then (catalog_number, label)."""
    proposals: list[GroupProposal] = []
    remaining: list[Track] = []
    used_ids: set[int] = set()

    def _cluster_by(key_fn: Callable[[Track], str | None], basis: str) -> None:
        buckets: dict[str, list[Track]] = defaultdict(list)
        for t in tracks:
            if t.id in used_ids:
                continue
            key_val = key_fn(t)
            if key_val:
                buckets[key_val].append(t)
        for key_val, members in buckets.items():
            if len(members) < 1:
                continue
            first = members[0]
            proposals.append(
                GroupProposal(
                    key=_group_key(basis, key_val),
                    kind="album" if len(members) > 1 else "unknown",
                    grouping_basis=basis,
                    grouping_confidence=1.0,
                    track_ids=tuple(t.id for t in members),
                    album=first.album,
                    album_artist=first.album_artist,
                    year=first.year,
                    label=first.label,
                    catalog_number=first.catalog_number,
                    barcode=first.barcode,
                    mb_release_id=first.mb_release_id,
                )
            )
            used_ids.update(t.id for t in members)

    _cluster_by(lambda t: t.mb_release_id, "release_id")
    _cluster_by(lambda t: t.barcode, "barcode")
    _cluster_by(
        lambda t: f"{t.catalog_number}|{t.label}" if t.catalog_number and t.label else None,
        "catalog_label",
    )

    remaining = [t for t in tracks if t.id not in used_ids]
    return proposals, remaining


def _stage2_fingerprint_consensus(
    tracks: list[Track], fingerprint_matches: dict[int, list[TrackFingerprintMatch]]
) -> tuple[list[GroupProposal], list[Track]]:
    """Group tracks whose AcoustID lookups independently agree on the
    same release MBID (docs/PLAN.md §7b Stage 2).

    For each track, collect every release MBID any of its fingerprint
    candidates points at (a track can have several plausible AcoustID
    matches; all their release lists count as "this track's votes").
    A release MBID that gets votes from >=3 tracks, or from >=50% of
    the tracks that have *any* fingerprint data at all, forms a group.
    Tracks with no fingerprint match data simply pass through
    untouched — this stage only ever adds confidence, never penalizes
    files fingerprinting hasn't run on yet.
    """
    votes_by_release: dict[str, set[int]] = defaultdict(set)
    track_release_votes: dict[int, set[str]] = {}

    for t in tracks:
        matches = fingerprint_matches.get(t.id, [])
        if not matches:
            continue
        release_ids = {rid for m in matches for rid in m.mb_release_ids}
        if not release_ids:
            continue
        track_release_votes[t.id] = release_ids
        for rid in release_ids:
            votes_by_release[rid].add(t.id)

    n_fingerprinted = len(track_release_votes)
    threshold = max(_FINGERPRINT_MIN_ABSOLUTE, int(n_fingerprinted * _FINGERPRINT_MIN_FRACTION))
    # Never require more corroborating tracks than actually have
    # fingerprint data, or a fully-fingerprinted 2-track EP could never
    # pass the absolute floor.
    threshold = min(threshold, n_fingerprinted) if n_fingerprinted else threshold

    used_ids: set[int] = set()
    proposals: list[GroupProposal] = []
    tracks_by_id = {t.id: t for t in tracks}

    # Sort by vote count descending so the most-corroborated release
    # claims its tracks first if a track's fingerprint matches happen
    # to point at more than one release above threshold.
    for release_id, voter_ids in sorted(
        votes_by_release.items(), key=lambda kv: len(kv[1]), reverse=True
    ):
        available = voter_ids - used_ids
        if len(available) < max(threshold, _FINGERPRINT_MIN_ABSOLUTE) and len(
            available
        ) < len(voter_ids) * _FINGERPRINT_MIN_FRACTION:
            continue
        if len(available) < 2:
            continue
        members = [tracks_by_id[tid] for tid in sorted(available)]
        first = members[0]
        proposals.append(
            GroupProposal(
                key=_group_key("fingerprint", release_id),
                kind="album",
                grouping_basis="fingerprint",
                grouping_confidence=0.9,
                track_ids=tuple(t.id for t in members),
                album=first.album,
                album_artist=first.album_artist,
                year=first.year,
                label=first.label,
                catalog_number=first.catalog_number,
                barcode=first.barcode,
                mb_release_id=release_id,
            )
        )
        used_ids.update(available)

    remaining = [t for t in tracks if t.id not in used_ids]
    return proposals, remaining


def _stage3_tag_clustering(tracks: list[Track]) -> tuple[list[GroupProposal], list[Track]]:
    """Fuzzy-cluster remaining tracks by (album_artist, album) using
    string_dist with a distance threshold rather than exact equality."""
    candidates = [t for t in tracks if not _is_singleton_track(t)]
    already_singleton = [t for t in tracks if t not in candidates]

    # Greedy single-link clustering: for each track, join the first
    # existing cluster whose representative is close enough, else start
    # a new cluster. O(n * clusters) — fine at library scale since the
    # number of distinct albums is much smaller than the track count.
    clusters: list[list[Track]] = []
    for t in candidates:
        joined = False
        for cluster in clusters:
            rep = cluster[0]
            artist_dist = string_dist(t.album_artist or t.artist, rep.album_artist or rep.artist)
            album_dist = string_dist(t.album, rep.album)
            combined = (artist_dist + album_dist) / 2
            if combined <= _TAG_CLUSTER_THRESHOLD:
                cluster.append(t)
                joined = True
                break
        if not joined:
            clusters.append([t])

    proposals: list[GroupProposal] = []
    remaining: list[Track] = []
    for cluster in clusters:
        if len(cluster) == 1:
            remaining.extend(cluster)
            continue
        rep = cluster[0]
        # Confidence scaled by intra-cluster tightness: tighter clusters
        # (near-identical tags) score near 0.85; looser fuzzy matches
        # near 0.5.
        dists = [
            (string_dist(t.album_artist or t.artist, rep.album_artist or rep.artist)
             + string_dist(t.album, rep.album)) / 2
            for t in cluster[1:]
        ]
        avg_dist = sum(dists) / len(dists) if dists else 0.0
        confidence = round(0.85 - avg_dist * (0.35 / max(_TAG_CLUSTER_THRESHOLD, 1e-9)), 3)
        confidence = max(0.5, min(0.85, confidence))
        discriminator = f"{normalize_for_match(rep.album_artist or rep.artist)}|{normalize_for_match(rep.album)}"
        proposals.append(
            GroupProposal(
                key=_group_key("tags", discriminator),
                kind="album",
                grouping_basis="tags",
                grouping_confidence=confidence,
                track_ids=tuple(t.id for t in cluster),
                album=rep.album,
                album_artist=rep.album_artist,
                year=rep.year,
                label=rep.label,
                catalog_number=rep.catalog_number,
                barcode=rep.barcode,
                mb_release_id=rep.mb_release_id,
            )
        )

    remaining.extend(already_singleton)
    return proposals, remaining


def _stage4_singletons(tracks: list[Track]) -> list[GroupProposal]:
    """Everything not swept into an album-shaped group becomes its own
    singleton group (basis='singleton')."""
    proposals = []
    for t in tracks:
        discriminator = f"track:{t.id}"
        proposals.append(
            GroupProposal(
                key=_group_key("singleton", discriminator),
                kind="singleton",
                grouping_basis="singleton",
                grouping_confidence=1.0,
                track_ids=(t.id,),
                album=t.album,
                album_artist=t.album_artist or t.artist,
                year=t.year,
            )
        )
    return proposals


def _apply_partial_album_flag(
    proposals: list[GroupProposal], tracks_by_id: dict[int, Track]
) -> list[GroupProposal]:
    """Flags a cluster `partial_album` when its own members' tag-level
    `track_total` agrees on an expected count larger than the cluster
    itself holds.

    A real release match (Phase 3's `expected_track_count`, sourced
    from a provider) is the stronger signal the plan describes, but
    that doesn't exist pre-matching. This is the one signal available
    from tags alone: if every track in a Stage-1/3 cluster agrees
    `track_total=12` and the cluster only has 3 members, that is
    exactly the "3 of 12 tracks present" case docs/PLAN.md §7b
    describes — muzilla never guesses which explanation is true
    (incomplete rip vs. deliberate subset), it just surfaces the count.
    """
    result = []
    for p in proposals:
        if p.kind != "album" or len(p.track_ids) < 2:
            result.append(p)
            continue
        totals = {
            tracks_by_id[tid].track_total
            for tid in p.track_ids
            if tracks_by_id.get(tid) is not None and tracks_by_id[tid].track_total
        }
        if len(totals) == 1:
            (expected,) = totals
            if expected and expected > len(p.track_ids):
                result.append(
                    GroupProposal(
                        key=p.key,
                        kind="partial_album",
                        grouping_basis=p.grouping_basis,
                        grouping_confidence=p.grouping_confidence,
                        track_ids=p.track_ids,
                        album=p.album,
                        album_artist=p.album_artist,
                        year=p.year,
                        label=p.label,
                        catalog_number=p.catalog_number,
                        barcode=p.barcode,
                        mb_release_id=p.mb_release_id,
                        expected_track_count=expected,
                    )
                )
                continue
        result.append(p)
    return result


def run_grouping_cascade(session: Session) -> GroupingRunResult:
    """Runs the full cascade over every ungrouped-or-unpinned track and
    persists proposals to `track_groups`.

    Tracks already in a pinned group are excluded entirely — pins are
    sticky across rescans (docs/PLAN.md §7b). Tracks in an existing
    unpinned group are re-considered (the cascade may propose a better
    grouping as more tags/matches accumulate over time).
    """
    all_tracks = list(session.scalars(select(Track).where(Track.missing_since.is_(None))))

    pinned_group_ids = {
        g.id for g in session.scalars(select(TrackGroup).where(TrackGroup.is_pinned))
    }
    eligible = [t for t in all_tracks if t.group_id not in pinned_group_ids]
    skipped_pinned = len(all_tracks) - len(eligible)

    stage1_proposals, remaining = _stage1_strong_identifiers(eligible)
    # Stage 1 proposals of size 1 aren't confidently "album" yet — let
    # them fall through to tag clustering/singleton classification
    # rather than parking as a lone unknown-kind group.
    stage1_multi = [p for p in stage1_proposals if len(p.track_ids) > 1]
    stage1_singles_track_ids = {
        tid for p in stage1_proposals if len(p.track_ids) == 1 for tid in p.track_ids
    }
    remaining = remaining + [t for t in eligible if t.id in stage1_singles_track_ids]

    fingerprint_matches: dict[int, list[TrackFingerprintMatch]] = defaultdict(list)
    if remaining:
        remaining_ids = {t.id for t in remaining}
        # Batched IN() rather than one query with all of remaining_ids as
        # bind parameters: SQLite's SQLITE_MAX_VARIABLE_NUMBER is 999 by
        # default (older SQLite) or 32766 (SQLite >=3.32, this repo's dev
        # environment), so a single `track_id.in_(remaining_ids)` call
        # raises "too many SQL variables" once a library-wide cascade run
        # has enough tracks reach this stage -- confirmed at 100k tracks
        # against this environment's SQLite build (docs/PLAN.md §11g); the
        # exact ceiling varies by SQLite build/compile flags, so batching
        # at a fixed 500 stays safely under either limit rather than
        # depending on runtime detection. Never triggered by any test
        # before this, since nothing exercised the cascade above fixture
        # scale.
        remaining_ids_list = list(remaining_ids)
        batch_size = 500
        for i in range(0, len(remaining_ids_list), batch_size):
            batch = remaining_ids_list[i : i + batch_size]
            for match in session.scalars(
                select(TrackFingerprintMatch).where(TrackFingerprintMatch.track_id.in_(batch))
            ):
                fingerprint_matches[match.track_id].append(match)

    stage2_proposals, remaining = _stage2_fingerprint_consensus(remaining, fingerprint_matches)
    stage3_proposals, remaining = _stage3_tag_clustering(remaining)
    stage4_proposals = _stage4_singletons(remaining)

    tracks_by_id = {t.id: t for t in all_tracks}
    all_proposals = (
        _apply_partial_album_flag(
            stage1_multi + stage2_proposals + stage3_proposals, tracks_by_id
        )
        + stage4_proposals
    )

    groups_created = 0
    groups_updated = 0
    tracks_grouped = 0

    existing_by_key = {g.key: g for g in session.scalars(select(TrackGroup))}

    for proposal in all_proposals:
        group = existing_by_key.get(proposal.key)
        if group is None:
            group = TrackGroup(key=proposal.key)
            session.add(group)
            groups_created += 1
        elif group.is_pinned:
            continue  # never overwrite a pinned group's fields
        else:
            groups_updated += 1

        group.kind = proposal.kind
        group.grouping_basis = proposal.grouping_basis
        group.grouping_confidence = proposal.grouping_confidence
        group.album = proposal.album
        group.album_artist = proposal.album_artist
        group.year = proposal.year
        group.track_count = len(proposal.track_ids)
        group.label = proposal.label
        group.catalog_number = proposal.catalog_number
        group.barcode = proposal.barcode
        group.mb_release_id = proposal.mb_release_id
        group.expected_track_count = proposal.expected_track_count
        group.updated_at = datetime.now(UTC)
        session.flush()

        for track_id in proposal.track_ids:
            track = session.get(Track, track_id)
            if track is not None and track.group_id != group.id:
                track.group_id = group.id
                tracks_grouped += 1

        existing_by_key[proposal.key] = group

    session.flush()

    return GroupingRunResult(
        proposals=tuple(all_proposals),
        groups_created=groups_created,
        groups_updated=groups_updated,
        tracks_grouped=tracks_grouped,
        tracks_skipped_pinned=skipped_pinned,
    )
