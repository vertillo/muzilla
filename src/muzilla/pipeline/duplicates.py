"""Fingerprint-based duplicate detection: finding the same track at
different bitrates in a flat folder of mixed-era rips.

Detection only — see db/models.py's DuplicateGroup docstring for why
there is no delete/resolve action here. Persists results to
duplicate_groups/duplicate_members so the API/UI can list them without
recomputing on every request; re-running is cheap and idempotent
(groups are keyed by mb_recording_id), so this is safe to run
repeatedly as fingerprinting fills in over time.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import DuplicateGroup, DuplicateMember, Track, TrackFingerprintMatch


@dataclass(frozen=True, slots=True)
class DuplicateDetectionResult:
    groups_created: int
    groups_updated: int
    groups_dismissed_skipped: int
    """Existing dismissed groups left untouched — a dismissal must not
    be silently undone by the next detection run."""
    groups_removed: int
    """Non-dismissed groups deleted because a re-fingerprint moved
    enough tracks away that fewer than 2 remain — no longer a duplicate."""


def _best_recording_id(matches: list[TrackFingerprintMatch]) -> str | None:
    """A track can have several candidate recordings from AcoustID;
    the highest-scoring one is what "this track's recording" means for
    duplicate purposes."""
    if not matches:
        return None
    return max(matches, key=lambda m: m.score).mb_recording_id


def detect_duplicates(session: Session) -> DuplicateDetectionResult:
    """Scans every fingerprinted track, groups by best-match recording
    id, and upserts a `DuplicateGroup` for every recording id currently
    shared by 2+ distinct tracks. Every *existing* group is also
    reconciled against the current membership (not just ones that still
    qualify), so a group a re-fingerprint has shrunk below 2 tracks
    gets removed rather than left stale with a dangling reference."""
    tracks = list(session.scalars(select(Track).where(Track.missing_since.is_(None))))
    matches_by_track: dict[int, list[TrackFingerprintMatch]] = defaultdict(list)
    for m in session.scalars(select(TrackFingerprintMatch)):
        matches_by_track[m.track_id].append(m)

    tracks_by_recording: dict[str, list[int]] = defaultdict(list)
    for track in tracks:
        recording_id = _best_recording_id(matches_by_track.get(track.id, []))
        if recording_id is not None:
            tracks_by_recording[recording_id].append(track.id)

    existing_groups = list(session.scalars(select(DuplicateGroup)))
    existing_by_recording = {g.mb_recording_id: g for g in existing_groups}

    created = 0
    updated = 0
    dismissed_skipped = 0
    removed = 0

    # Every recording id that either currently qualifies (2+ tracks) or
    # already has a group needs reconciling — the union covers both
    # "newly a duplicate" and "no longer a duplicate" transitions.
    all_recording_ids = set(tracks_by_recording) | set(existing_by_recording)

    for recording_id in all_recording_ids:
        track_ids = tracks_by_recording.get(recording_id, [])
        group = existing_by_recording.get(recording_id)

        if group is not None and group.dismissed:
            dismissed_skipped += 1
            continue

        if len(track_ids) < 2:
            if group is not None:
                session.delete(group)
                removed += 1
            continue

        if group is None:
            group = DuplicateGroup(mb_recording_id=recording_id, basis="acoustid")
            session.add(group)
            session.flush()
            created += 1
        else:
            updated += 1

        existing_member_track_ids = {m.track_id for m in group.members}
        for track_id in track_ids:
            if track_id not in existing_member_track_ids:
                # Append through the relationship (not a bare
                # session.add with group_id set directly) so
                # group.members stays in sync in-session — with
                # expire_on_commit=False a raw FK write leaves the
                # already-loaded collection stale (a relationship/identity-map
                # behavior covered by the regression tests).
                group.members.append(DuplicateMember(track_id=track_id))
        # Drop members for tracks that no longer share this recording id
        # (a re-fingerprint changed the best match, or the track vanished).
        # Same reasoning in reverse: remove through the collection, not
        # a bare session.delete(), or group.members stays stale in-session.
        stale = existing_member_track_ids - set(track_ids)
        if stale:
            for member in [m for m in group.members if m.track_id in stale]:
                group.members.remove(member)
                session.delete(member)

    session.flush()
    return DuplicateDetectionResult(
        groups_created=created,
        groups_updated=updated,
        groups_dismissed_skipped=dismissed_skipped,
        groups_removed=removed,
    )
