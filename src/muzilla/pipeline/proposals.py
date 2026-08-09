"""Compose one file-first ReviewBundle from independent proposal sections.

The composer is intentionally synchronous and persistence-oriented: provider I/O and
ReplayGain remain in their own job handlers.  Each handler supplies a finished analysis
to this module, which replaces the immutable revision under the *same* bundle identity.
Nothing in here writes a media file.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.builder import FieldEdit
from muzilla.config.schema import PathsConfig
from muzilla.db.models import Operation, ProposalRevision, ReviewBundle, Track, TrackGroup
from muzilla.domain import fields as field_registry
from muzilla.domain.reviews import BundleState, OperationKind
from muzilla.pipeline import paths as paths_service
from muzilla.pipeline import reviews
from muzilla.pipeline.cover_assets import get_candidate
from muzilla.pipeline.matching import candidate_edits_for_group, candidate_edits_for_track
from muzilla.providers.base import ReleaseCandidate


class ProposalCompositionError(ValueError):
    pass


def _current_operations(session: Session, bundle_id: int) -> tuple[reviews.OperationDraft, ...]:
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle_id,
            ProposalRevision.is_current.is_(True),
        )
    )
    if revision is None:
        return ()
    return tuple(
        reviews.OperationDraft(
            kind=operation.kind,
            field=operation.field,
            target_type=operation.target_type,
            target_id=operation.target_id,
            current_value=operation.current_value,
            proposed_value=operation.proposed_value,
            provenance=operation.provenance,
            validation=operation.validation,
        )
        for operation in revision.operations
    )


def _snapshot(tracks: Iterable[Track]) -> dict[str, object]:
    return {
        "items": [
            {
                "source_type": "track",
                "source_id": track.id,
                "path": track.path,
                "filename": track.filename,
                "size_bytes": track.size_bytes,
                "mtime_ns": track.mtime_ns,
                "content_hash": track.content_hash,
                "tag_hash": track.tag_hash,
            }
            for track in tracks
        ]
    }


def _metadata_operations(
    tracks: list[Track], edits_by_track: dict[int, list[FieldEdit]], source: str
) -> tuple[reviews.OperationDraft, ...]:
    result: list[reviews.OperationDraft] = []
    for track in tracks:
        for edit in edits_by_track[track.id]:
            field = edit.field
            result.append(
                reviews.OperationDraft(
                    kind=OperationKind.SET_TAG,
                    field=field,
                    target_type="track",
                    target_id=track.id,
                    current_value=getattr(track, field),
                    proposed_value=edit.new_value,
                    provenance={"provider": source, "section": "metadata"},
                )
            )
    return tuple(result)


def _move_operations(
    session: Session,
    tracks: list[Track],
    metadata: tuple[reviews.OperationDraft, ...],
    config: PathsConfig,
) -> tuple[reviews.OperationDraft, ...]:
    proposed_by_track: dict[int, dict[str, object]] = {track.id: {} for track in tracks}
    for operation in metadata:
        proposed_by_track[operation.target_id][operation.field] = operation.proposed_value
    rows = paths_service.preview_rename(
        session,
        track_ids=[track.id for track in tracks],
        config=config,
        proposed_values_by_track_id=proposed_by_track,
    )
    return tuple(
        reviews.OperationDraft(
            kind=OperationKind.MOVE_FILE,
            field="path",
            target_type="track",
            target_id=row.track_id,
            current_value=row.old_path,
            proposed_value=row.new_path,
            provenance={"section": "path", "template": config.default},
            validation={"errors": list(row.errors), "collision": row.is_collision},
        )
        for row in rows
        if row.new_path != row.old_path
    )


def _non_metadata_operations(
    existing: tuple[reviews.OperationDraft, ...],
) -> tuple[reviews.OperationDraft, ...]:
    return tuple(
        operation
        for operation in existing
        if OperationKind(operation.kind) not in {OperationKind.SET_TAG, OperationKind.MOVE_FILE}
    )


def _existing_metadata_operations(
    existing: tuple[reviews.OperationDraft, ...],
) -> tuple[reviews.OperationDraft, ...]:
    return tuple(
        operation for operation in existing if OperationKind(operation.kind) is OperationKind.SET_TAG
    )


def _manual_metadata_operations(
    existing: tuple[reviews.OperationDraft, ...],
) -> tuple[reviews.OperationDraft, ...]:
    return tuple(
        operation
        for operation in _existing_metadata_operations(existing)
        if operation.provenance.get("source") == "manual"
    )


class ProposalComposer:
    """Single owner for combining sections into a stable review bundle."""

    def __init__(self, session: Session, *, paths_config: PathsConfig | None = None) -> None:
        self.session = session
        self.paths_config = paths_config or PathsConfig()

    def compose_candidate(
        self, review: ReviewBundle, candidate: ReleaseCandidate
    ) -> reviews.ReviewBundleDetail:
        if review.scope_id is None or review.scope_type not in {"track", "group"}:
            raise ProposalCompositionError("candidate composition requires a track or group review")
        if review.scope_type == "track":
            track = self.session.get(Track, review.scope_id)
            if track is None:
                raise ProposalCompositionError(f"track {review.scope_id} not found")
            tracks = [track]
            edits_by_track = {track.id: candidate_edits_for_track(track, candidate)}
        else:
            group = self.session.get(TrackGroup, review.scope_id)
            if group is None:
                raise ProposalCompositionError(f"group {review.scope_id} not found")
            tracks = list(group.tracks)
            edits_by_track = candidate_edits_for_group(group, candidate)

        candidate_metadata = _metadata_operations(tracks, edits_by_track, candidate.source)
        if not candidate_metadata:
            raise ProposalCompositionError(
                "candidate does not provide metadata applicable to this review"
            )
        existing = _current_operations(self.session, review.id)
        manual_metadata = _manual_metadata_operations(existing)
        manual_fields = {operation.field for operation in manual_metadata}
        metadata = tuple(
            operation for operation in candidate_metadata if operation.field not in manual_fields
        ) + manual_metadata
        operations = metadata + _move_operations(self.session, tracks, metadata, self.paths_config)
        operations += _non_metadata_operations(existing)
        write = reviews.put_revision(
            self.session,
            bundle_id=review.id,
            logical_key=review.logical_key,
            title=review.title,
            scope_type=review.scope_type,
            scope_id=review.scope_id,
            source_snapshot=_snapshot(tracks),
            operations=operations,
            candidate_source=candidate.source,
            candidate_ref=candidate.ref.id,
        )
        if review.state in {BundleState.PREPARING.value, BundleState.NEEDS_ATTENTION.value}:
            reviews.transition_bundle(self.session, write.bundle_id, BundleState.READY)
        detail = reviews.get_review_bundle(self.session, write.bundle_id)
        if detail is None:  # pragma: no cover - put_revision guarantees it
            raise ProposalCompositionError("could not load composed review")
        return detail

    def compose_candidate_for_scope(
        self,
        *,
        scope_type: str,
        scope_id: int,
        candidate: ReleaseCandidate,
    ) -> reviews.ReviewBundleDetail:
        """Create-or-reuse the one open review for a matching scope."""
        if scope_type not in {"track", "group"}:
            raise ProposalCompositionError("proposal scope must be track or group")
        logical_key = f"{scope_type}:{scope_id}"
        bundle = self.session.scalar(
            select(ReviewBundle).where(
                ReviewBundle.logical_key == logical_key,
                ReviewBundle.state.in_(
                    [
                        BundleState.PREPARING.value,
                        BundleState.READY.value,
                        BundleState.NEEDS_ATTENTION.value,
                    ]
                ),
            )
        )
        if bundle is None:
            if scope_type == "track":
                track = self.session.get(Track, scope_id)
                label = track.filename if track is not None else str(scope_id)
            else:
                group = self.session.get(TrackGroup, scope_id)
                label = (group.album or "Untitled") if group is not None else str(scope_id)
            bundle = ReviewBundle(
                logical_key=logical_key,
                title=f"Review {label}",
                scope_type=scope_type,
                scope_id=scope_id,
                state=BundleState.PREPARING.value,
            )
            self.session.add(bundle)
            self.session.flush()
        return self.compose_candidate(bundle, candidate)

    def compose_manual_track_edit(
        self, *, track_id: int, field_values: dict[str, object]
    ) -> reviews.ReviewBundleDetail:
        """Add explicit single-file edits to the same reviewed workflow.

        This is intentionally scoped to one track: collection-wide find/replace
        is not part of the primary catalog journey.
        """
        track = self.session.get(Track, track_id)
        if track is None:
            raise ProposalCompositionError(f"track {track_id} not found")
        if not field_values:
            raise ProposalCompositionError("choose at least one field to edit")
        operations: list[reviews.OperationDraft] = []
        for field, value in field_values.items():
            definition = field_registry.FIELDS.get(field)
            if definition is None or not definition.editable:
                raise ProposalCompositionError(f"field {field!r} is not editable")
            operations.append(
                reviews.OperationDraft(
                    kind=OperationKind.SET_TAG,
                    field=field,
                    target_type="track",
                    target_id=track.id,
                    current_value=getattr(track, field),
                    proposed_value=value,
                    provenance={"section": "metadata", "source": "manual"},
                )
            )
        logical_key = f"track:{track.id}"
        bundle = self.open_bundle_for_scope(self.session, scope_type="track", scope_id=track.id)
        title = bundle.title if bundle is not None else f"Review {track.filename}"
        existing = _current_operations(self.session, bundle.id) if bundle is not None else ()
        replaced_fields = set(field_values)
        metadata = tuple(
            operation
            for operation in _existing_metadata_operations(existing)
            if operation.field not in replaced_fields
        ) + tuple(operations)
        review_operations = metadata + _move_operations(
            self.session, [track], metadata, self.paths_config
        ) + _non_metadata_operations(existing)
        write = reviews.put_revision(
            self.session,
            bundle_id=bundle.id if bundle is not None else None,
            logical_key=logical_key,
            title=title,
            scope_type="track",
            scope_id=track.id,
            source_snapshot=_snapshot([track]),
            operations=review_operations,
            candidate_source=None,
            candidate_ref=None,
        )
        if bundle is None or bundle.state in {BundleState.PREPARING.value, BundleState.NEEDS_ATTENTION.value}:
            reviews.transition_bundle(self.session, write.bundle_id, BundleState.READY)
        detail = reviews.get_review_bundle(self.session, write.bundle_id)
        if detail is None:
            raise ProposalCompositionError("could not load manual review")
        return detail

    @staticmethod
    def open_bundle_for_scope(
        session: Session, *, scope_type: str, scope_id: int
    ) -> ReviewBundle | None:
        return session.scalar(
            select(ReviewBundle).where(
                ReviewBundle.logical_key == f"{scope_type}:{scope_id}",
                ReviewBundle.state.in_(
                    [
                        BundleState.PREPARING.value,
                        BundleState.READY.value,
                        BundleState.NEEDS_ATTENTION.value,
                    ]
                ),
            )
        )

    @classmethod
    def open_bundle_for_track(cls, session: Session, track: Track) -> ReviewBundle | None:
        direct = cls.open_bundle_for_scope(session, scope_type="track", scope_id=track.id)
        if direct is not None:
            return direct
        if track.group_id is not None:
            return cls.open_bundle_for_scope(session, scope_type="group", scope_id=track.group_id)
        return None

    @classmethod
    def open_bundle_for_group(cls, session: Session, group: TrackGroup) -> ReviewBundle | None:
        direct = cls.open_bundle_for_scope(session, scope_type="group", scope_id=group.id)
        if direct is not None:
            return direct
        return next(
            (
                cls.open_bundle_for_track(session, track)
                for track in group.tracks
                if cls.open_bundle_for_track(session, track) is not None
            ),
            None,
        )

    @staticmethod
    def proposed_tag_value(
        session: Session, bundle_id: int, *, track_id: int, field: str
    ) -> object | None:
        revision = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == bundle_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        if revision is None:
            return None
        return session.scalar(
            select(Operation.proposed_value).where(
                Operation.proposal_revision_id == revision.id,
                Operation.kind == OperationKind.SET_TAG.value,
                Operation.target_id == track_id,
                Operation.field == field,
            )
        )

    def add_operations(
        self,
        bundle_id: int,
        operations: tuple[reviews.OperationDraft, ...],
    ) -> reviews.ReviewBundleDetail:
        """Merge a finished section, replacing only equal kind/field/target rows."""
        bundle = self.session.get(ReviewBundle, bundle_id)
        if bundle is None:
            raise ProposalCompositionError(f"review bundle {bundle_id} not found")
        persisted_state = self.session.scalar(
            select(ReviewBundle.state).where(ReviewBundle.id == bundle_id)
        )
        if persisted_state == BundleState.DISCARDED.value:
            # The worker may have loaded ``bundle`` before another request
            # archived it.  Re-read the persisted state before promoting a
            # completed proposal.
            self.session.expire(bundle, ["state", "error"])
            current = reviews.get_review_bundle(self.session, bundle_id)
            if current is None:  # pragma: no cover - bundle was just loaded
                raise ProposalCompositionError("review bundle has no current revision")
            # A late task result must not countermand an explicit quick reject.
            # The task attempt records the completed work, but its proposal is
            # intentionally not promoted until the user explicitly reopens.
            return current
        current = reviews.get_review_bundle(self.session, bundle_id)
        if current is None:
            raise ProposalCompositionError("review bundle has no current revision")
        existing = _current_operations(self.session, bundle_id)
        incoming_keys = {
            (str(op.kind), op.field, op.target_type, op.target_id) for op in operations
        }
        combined = (
            tuple(
                op
                for op in existing
                if (str(op.kind), op.field, op.target_type, op.target_id) not in incoming_keys
            )
            + operations
        )
        tracks = self._scope_tracks(bundle)
        reviews.put_revision(
            self.session,
            bundle_id=bundle.id,
            logical_key=bundle.logical_key,
            title=bundle.title,
            scope_type=bundle.scope_type,
            scope_id=bundle.scope_id,
            source_snapshot=_snapshot(tracks),
            operations=combined,
            candidate_source=current.current_revision.candidate_source,
            candidate_ref=current.current_revision.candidate_ref,
        )
        detail = reviews.get_review_bundle(self.session, bundle_id)
        if detail is None:  # pragma: no cover
            raise ProposalCompositionError("could not load composed review")
        return detail

    def task_item_keys(self, bundle_id: int, *, kind: str) -> tuple[str, ...]:
        """Return the independently reportable items for one optional section."""
        if kind not in {"cover", "lyrics", "replaygain"}:
            raise ProposalCompositionError(f"unsupported proposal task kind: {kind}")
        bundle = self.session.get(ReviewBundle, bundle_id)
        if bundle is None:
            raise ProposalCompositionError(f"review bundle {bundle_id} not found")
        tracks = [track for track in self._scope_tracks(bundle) if track.missing_since is None]
        if kind == "lyrics":
            return tuple(f"track:{track.id}" for track in tracks)
        if bundle.scope_id is None:
            raise ProposalCompositionError("review bundle has no scope")
        return (f"{bundle.scope_type}:{bundle.scope_id}",)

    def plan_task_attempts(self, bundle_id: int, *, kind: str, job_id: int) -> tuple[int, ...]:
        """Plan every item in a section while the enqueue transaction is open."""
        return tuple(
            reviews.plan_task_attempt(
                self.session,
                bundle_id,
                kind=kind,
                item_key=item_key,
                job_id=job_id,
            ).id
            for item_key in self.task_item_keys(bundle_id, kind=kind)
        )

    def choose_cover(
        self, bundle_id: int, *, action: str, asset_candidate_id: int | None = None
    ) -> reviews.ReviewBundleDetail:
        """Apply the non-mutating cover decision to the current proposal.

        ``keep`` removes the proposed art section, ``remove`` proposes removal
        of current art, and ``select`` references a validated candidate owned
        by this bundle. Selection never accepts raw bytes or a global blob ID.
        """
        bundle = self.session.get(ReviewBundle, bundle_id)
        if bundle is None:
            raise ProposalCompositionError(f"review bundle {bundle_id} not found")
        if action not in {"keep", "select", "remove"}:
            raise ProposalCompositionError("cover action must be keep, select, or remove")
        candidate = None
        if action == "select":
            if asset_candidate_id is None:
                raise ProposalCompositionError("select requires an asset candidate")
            candidate = get_candidate(self.session, bundle_id, asset_candidate_id)
            if candidate is None:
                raise ProposalCompositionError(
                    "selected cover candidate does not belong to this review"
                )
        tracks = self._scope_tracks(bundle)
        existing = tuple(
            operation
            for operation in _current_operations(self.session, bundle_id)
            if OperationKind(operation.kind)
            not in {OperationKind.EMBED_ART, OperationKind.REMOVE_ART}
        )
        art_operations: tuple[reviews.OperationDraft, ...] = ()
        if action == "select":
            assert candidate is not None
            blob = candidate.blob
            art_operations = tuple(
                reviews.OperationDraft(
                    kind=OperationKind.EMBED_ART,
                    field="art",
                    target_type="track",
                    target_id=track.id,
                    current_value=(
                        {"blob_id": track.art_blob_id} if track.art_blob_id is not None else None
                    ),
                    proposed_value={"blob_id": blob.id},
                    provenance={
                        "section": "cover",
                        "provider": candidate.provider,
                        "asset_candidate_id": candidate.id,
                        "width": blob.width,
                        "height": blob.height,
                        "mime": blob.mime,
                    },
                )
                for track in tracks
                if track.missing_since is None
            )
        elif action == "remove":
            art_operations = tuple(
                reviews.OperationDraft(
                    kind=OperationKind.REMOVE_ART,
                    field="art",
                    target_type="track",
                    target_id=track.id,
                    current_value=(
                        {"blob_id": track.art_blob_id} if track.art_blob_id is not None else None
                    ),
                    proposed_value=None,
                    provenance={"section": "cover", "decision": "remove"},
                )
                for track in tracks
                if track.missing_since is None
            )
        current = reviews.get_review_bundle(self.session, bundle_id)
        if current is None:
            raise ProposalCompositionError("review bundle has no current revision")
        reviews.put_revision(
            self.session,
            bundle_id=bundle.id,
            logical_key=bundle.logical_key,
            title=bundle.title,
            scope_type=bundle.scope_type,
            scope_id=bundle.scope_id,
            source_snapshot=_snapshot(tracks),
            operations=existing + art_operations,
            candidate_source=current.current_revision.candidate_source,
            candidate_ref=current.current_revision.candidate_ref,
        )
        detail = reviews.get_review_bundle(self.session, bundle_id)
        if detail is None:  # pragma: no cover
            raise ProposalCompositionError("could not load composed review")
        return detail

    def _scope_tracks(self, bundle: ReviewBundle) -> list[Track]:
        if bundle.scope_id is None:
            raise ProposalCompositionError("review bundle has no scope")
        if bundle.scope_type == "track":
            track = self.session.get(Track, bundle.scope_id)
            if track is None:
                raise ProposalCompositionError(f"track {bundle.scope_id} not found")
            return [track]
        if bundle.scope_type == "group":
            group = self.session.get(TrackGroup, bundle.scope_id)
            if group is None:
                raise ProposalCompositionError(f"group {bundle.scope_id} not found")
            return list(group.tracks)
        raise ProposalCompositionError("review bundle has unsupported scope")
