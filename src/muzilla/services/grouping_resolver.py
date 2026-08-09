"""Constrained, review-first resolution for an uncertain collection inference.

The grouping engine remains an internal inference mechanism.  This service exposes only
the small set of reversible choices that can be justified from the track and its inferred
collection; it never changes ``Track.group_id`` itself.
"""

from __future__ import annotations

from hashlib import blake2b

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackGroup
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import (
    OperationDraft,
    ReviewBundleDetail,
    ReviewInvariantError,
    get_review_bundle,
    put_revision,
    transition_bundle,
)

UNCERTAIN_GROUPING_CONFIDENCE = 0.8


class GroupingResolverError(ValueError):
    pass


def _identity(value: str | None) -> str:
    return " ".join((value or "").split()).casefold()


def _same_collection(track: Track, group: TrackGroup) -> bool:
    """A destination is eligible only when its human collection identity agrees.

    This intentionally has no fuzzy fallback: a resolver may offer fewer choices, but
    it must never turn an uncertain cluster into arbitrary cross-album reassignment.
    """
    track_artist = _identity(track.album_artist or track.artist)
    return bool(
        _identity(track.album)
        and track_artist
        and _identity(track.album) == _identity(group.album)
        and track_artist == _identity(group.album_artist)
    )


def _source_snapshot(track: Track) -> dict[str, object]:
    return {
        "items": [
            {
                "source_type": "track",
                "source_id": track.id,
                "filename": track.filename,
                "path": track.path,
                "size_bytes": track.size_bytes,
                "mtime_ns": track.mtime_ns,
                "content_hash": track.content_hash,
                "tag_hash": track.tag_hash,
            }
        ]
    }


def _operation(
    track: Track,
    source: TrackGroup,
    *,
    proposed_value: dict[str, object],
    label: str,
) -> OperationDraft:
    return OperationDraft(
        kind="grouping_correction",
        field="collection",
        target_type="track",
        target_id=track.id,
        current_value={"group_id": source.id},
        proposed_value=proposed_value,
        provenance={"source": "uncertain_grouping", "label": label},
        validation={
            "compatible": True,
            "preview": {
                "current_collection": source.album or "Raccolta senza nome",
                "action": proposed_value["action"],
                "label": label,
            },
        },
    )


def create_grouping_review(session: Session, track_id: int) -> ReviewBundleDetail:
    """Create or refresh one non-mutating ReviewBundle for an uncertain track.

    Choices are deliberately constrained to confirming the current collection,
    treating the file as a singleton, and moving it only to a separately inferred
    collection with exactly the same normalized album and album-artist identity.
    """
    track = session.get(Track, track_id)
    if track is None:
        raise GroupingResolverError(f"track {track_id} not found")
    if track.group_id is None:
        raise GroupingResolverError("track has no inferred collection to resolve")
    source = session.get(TrackGroup, track.group_id)
    if source is None:  # pragma: no cover - protected by the application invariant
        raise GroupingResolverError("track collection no longer exists")
    if source.is_pinned:
        raise GroupingResolverError("track collection is already pinned")
    if (
        source.grouping_confidence is None
        or source.grouping_confidence >= UNCERTAIN_GROUPING_CONFIDENCE
    ):
        raise GroupingResolverError("track collection is not uncertain")

    singleton_key = blake2b(f"resolver-singleton:{track.id}".encode()).hexdigest()[:32]
    operations: list[OperationDraft] = [
        _operation(
            track,
            source,
            proposed_value={"action": "confirm_collection", "source_group_id": source.id},
            label="Conferma questa raccolta",
        ),
        _operation(
            track,
            source,
            proposed_value={
                "action": "treat_as_singleton",
                "source_group_id": source.id,
                "singleton_key": singleton_key,
            },
            label="Tratta come brano singolo",
        ),
    ]
    compatible_groups = session.scalars(
        select(TrackGroup)
        .where(TrackGroup.id != source.id, TrackGroup.track_count > 0)
        .order_by(TrackGroup.id)
    )
    for candidate in compatible_groups:
        if not _same_collection(track, candidate):
            continue
        operations.append(
            _operation(
                track,
                source,
                proposed_value={
                    "action": "move_to_collection",
                    "source_group_id": source.id,
                    "to_group_id": candidate.id,
                },
                label=f"Usa la raccolta {candidate.album or 'senza nome'}",
            )
        )

    try:
        write = put_revision(
            session,
            logical_key=f"track:{track.id}:grouping",
            title=f"Risolvi raccolta: {track.filename}",
            scope_type="track",
            scope_id=track.id,
            source_snapshot=_source_snapshot(track),
            operations=tuple(operations),
        )
        transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)
    except ReviewInvariantError as exc:
        raise GroupingResolverError(str(exc)) from exc
    detail = get_review_bundle(session, write.bundle_id)
    assert detail is not None
    return detail
