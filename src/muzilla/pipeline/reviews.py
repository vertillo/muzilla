"""Transactional foundation for stable ReviewBundles and immutable revisions.

No production flow is migrated here.  This service is the one-way destination for later
producer adapters; legacy ChangeSets continue to use their existing read/apply path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from muzilla.db.models import (
    ApplyRun,
    AssetCandidate,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ReviewBundle,
    SourceSnapshot,
    TaskAttempt,
)
from muzilla.domain.reviews import (
    ACTIVE_BUNDLE_STATES,
    BundleState,
    InvalidBundleTransition,
    OperationKind,
    validate_transition,
)


class ReviewInvariantError(ValueError):
    pass


class NoAcceptedOperationsError(ReviewInvariantError):
    pass


class ReviewTasksPendingError(ReviewInvariantError):
    """Apply readiness warning for optional work that is not terminal yet."""

    def __init__(self, attempts: tuple[tuple[str, str, str], ...]) -> None:
        self.attempts = attempts
        labels = ", ".join(f"{kind}:{item_key} ({state})" for kind, item_key, state in attempts)
        super().__init__(f"review has unfinished optional tasks: {labels}")


@dataclass(frozen=True, slots=True)
class OperationDraft:
    kind: OperationKind | str
    field: str
    target_type: str
    target_id: int
    current_value: object | None
    proposed_value: object | None
    provenance: dict[str, object] = dataclass_field(default_factory=dict)
    validation: dict[str, object] = dataclass_field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RevisionWrite:
    bundle_id: int
    revision_id: int
    created_bundle: bool
    created_revision: bool


@dataclass(frozen=True, slots=True)
class ReviewOperationDetail:
    id: int
    seq: int
    kind: str
    field: str
    target_type: str
    target_id: int
    current_value: object | None
    proposed_value: object | None
    decision: str
    provenance: dict[str, object]
    validation: dict[str, object]


@dataclass(frozen=True, slots=True)
class ProposalRevisionDetail:
    id: int
    revision_no: int
    content_digest: str
    candidate_source: str | None
    candidate_ref: str | None
    created_at: datetime
    operations: tuple[ReviewOperationDetail, ...]


@dataclass(frozen=True, slots=True)
class OperationAttemptDetail:
    operation_id: int
    attempted_value: object | None
    state: str
    error: str | None


@dataclass(frozen=True, slots=True)
class ApplyRunDetail:
    id: int
    revision_id: int
    state: str
    result: dict[str, object] | None
    error: str | None
    operation_attempts: tuple[OperationAttemptDetail, ...]


@dataclass(frozen=True, slots=True)
class TaskAttemptDetail:
    """The outcome of one independently scheduled proposal section.

    A task attempt is intentionally not an operation attempt: it describes
    acquiring/analyzing data, whereas operations stay a reviewed, non-mutating
    proposal until the apply path runs.
    """

    id: int
    kind: str
    item_key: str
    state: str
    attempt_no: int
    job_id: int | None
    result: dict[str, object] | None
    error: str | None


@dataclass(frozen=True, slots=True)
class AssetCandidateDetail:
    id: int
    blob_id: int
    provider: str
    mime: str
    size: int
    width: int
    height: int
    thumbnail_url: str


@dataclass(frozen=True, slots=True)
class ReviewBundleDetail:
    id: int
    logical_key: str
    title: str
    scope_type: str
    scope_id: int | None
    state: str
    error: str | None
    current_revision: ProposalRevisionDetail
    cover_candidates: tuple[AssetCandidateDetail, ...]
    task_attempts: tuple[TaskAttemptDetail, ...]
    apply_runs: tuple[ApplyRunDetail, ...]


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReviewInvariantError("review content must be finite JSON data") from exc


def _json_copy(value: object) -> object:
    return json.loads(_canonical_json(value))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _normalize_operation(operation: OperationDraft) -> dict[str, object]:
    try:
        kind = OperationKind(operation.kind)
    except ValueError as exc:
        raise ReviewInvariantError(f"unsupported operation kind: {operation.kind!r}") from exc

    if not operation.field.strip():
        raise ReviewInvariantError("operation field must not be empty")
    current_value = _json_copy(operation.current_value)
    proposed_value = _json_copy(operation.proposed_value)
    if kind is OperationKind.WRITE_LYRICS:
        _validate_lyrics_value(proposed_value, "proposed_value")
        if current_value is not None:
            _validate_lyrics_value(current_value, "current_value")
    elif kind is OperationKind.EMBED_ART:
        _validate_art_value(proposed_value, "proposed_value")
        if current_value is not None:
            _validate_art_value(current_value, "current_value")
    elif kind is OperationKind.REMOVE_ART:
        if proposed_value is not None:
            raise ReviewInvariantError("remove_art proposed_value must be null")
        if current_value is not None:
            _validate_art_value(current_value, "current_value")
    elif kind is OperationKind.MOVE_FILE:
        if not isinstance(current_value, str) or not isinstance(proposed_value, str):
            raise ReviewInvariantError("move_file requires string current/proposed values")
    elif kind is OperationKind.SET_REPLAY_GAIN:
        if current_value is not None:
            _validate_number(current_value, "set_replay_gain current_value")
        _validate_number(proposed_value, "set_replay_gain proposed_value")

    return {
        "kind": kind.value,
        "field": operation.field,
        "target_type": operation.target_type,
        "target_id": operation.target_id,
        "current_value": current_value,
        "proposed_value": proposed_value,
        "provenance": _json_copy(operation.provenance),
        "validation": _json_copy(operation.validation),
    }


def _persisted_operation_content(operation: Operation) -> dict[str, object]:
    return {
        "kind": operation.kind,
        "field": operation.field,
        "target_type": operation.target_type,
        "target_id": operation.target_id,
        "current_value": operation.current_value,
        "proposed_value": operation.proposed_value,
        "provenance": operation.provenance,
        "validation": operation.validation,
    }


def _validate_lyrics_value(value: object, name: str) -> None:
    if not isinstance(value, dict):
        raise ReviewInvariantError(f"write_lyrics {name} must be an object")
    text_value = value.get("text")
    synced_value = value.get("synced")
    provider_value = value.get("provider")
    if (
        not isinstance(text_value, str)
        or not isinstance(synced_value, bool)
        or not isinstance(provider_value, str)
    ):
        raise ReviewInvariantError("write_lyrics requires string text/provider and boolean synced")


def _validate_art_value(value: object, name: str) -> None:
    if not isinstance(value, dict):
        raise ReviewInvariantError(f"art {name} must be an object")
    blob_id = value.get("blob_id")
    if not isinstance(blob_id, int) or isinstance(blob_id, bool):
        raise ReviewInvariantError(f"art {name} requires an integer blob_id")


def _validate_number(value: object, name: str) -> None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise ReviewInvariantError(f"{name} must be numeric")


def _active_bundle(session: Session, logical_key: str) -> ReviewBundle | None:
    active_values = tuple(state.value for state in ACTIVE_BUNDLE_STATES)
    return session.scalar(
        select(ReviewBundle).where(
            ReviewBundle.logical_key == logical_key,
            ReviewBundle.state.in_(active_values),
        )
    )


def _current_revision(session: Session, bundle_id: int) -> ProposalRevision | None:
    return session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle_id,
            ProposalRevision.is_current.is_(True),
        )
    )


def get_review_bundle(session: Session, bundle_id: int) -> ReviewBundleDetail | None:
    """Return the stable review identity with its current immutable revision.

    Attempt values are deliberately exposed below the apply run rather than folded into
    an operation: observed ``current_value``, reviewed ``proposed_value``, and an apply
    attempt are three different facts with different lifecycles.
    """
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        return None
    revision = _current_revision(session, bundle_id)
    if revision is None:
        return None

    operations = tuple(
        ReviewOperationDetail(
            id=operation.id,
            seq=operation.seq,
            kind=operation.kind,
            field=operation.field,
            target_type=operation.target_type,
            target_id=operation.target_id,
            current_value=operation.current_value,
            proposed_value=operation.proposed_value,
            decision=operation.decision,
            provenance=operation.provenance,
            validation=operation.validation,
        )
        for operation in revision.operations
    )
    apply_runs = tuple(
        ApplyRunDetail(
            id=run.id,
            revision_id=run.proposal_revision_id,
            state=run.state,
            result=run.result,
            error=run.error,
            operation_attempts=tuple(
                OperationAttemptDetail(
                    operation_id=attempt.operation_id,
                    attempted_value=attempt.attempted_value,
                    state=attempt.state,
                    error=attempt.error,
                )
                for attempt in run.operation_attempts
            ),
        )
        for run in bundle.apply_runs
    )
    task_attempts = tuple(
        TaskAttemptDetail(
            id=attempt.id,
            kind=attempt.kind,
            item_key=attempt.item_key,
            state=attempt.state,
            attempt_no=attempt.attempt_no,
            job_id=attempt.job_id,
            result=attempt.result,
            error=attempt.error,
        )
        for attempt in sorted(
            bundle.task_attempts,
            key=lambda item: (item.kind, item.item_key, item.attempt_no),
        )
    )
    cover_candidates = tuple(
        _asset_candidate_detail(candidate)
        for candidate in sorted(bundle.asset_candidates, key=lambda item: item.id)
    )
    return ReviewBundleDetail(
        id=bundle.id,
        logical_key=bundle.logical_key,
        title=bundle.title,
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        state=bundle.state,
        error=bundle.error,
        current_revision=ProposalRevisionDetail(
            id=revision.id,
            revision_no=revision.revision_no,
            content_digest=revision.content_digest,
            candidate_source=revision.candidate_source,
            candidate_ref=revision.candidate_ref,
            created_at=revision.created_at,
            operations=operations,
        ),
        cover_candidates=cover_candidates,
        task_attempts=task_attempts,
        apply_runs=apply_runs,
    )


def _asset_candidate_detail(candidate: AssetCandidate) -> AssetCandidateDetail:
    blob = candidate.blob
    if blob.width is None or blob.height is None:
        raise ReviewInvariantError("cover candidate is missing validated dimensions")
    return AssetCandidateDetail(
        id=candidate.id,
        blob_id=blob.id,
        provider=candidate.provider,
        mime=blob.mime,
        size=blob.size,
        width=blob.width,
        height=blob.height,
        thumbnail_url=(
            f"/api/reviews/{candidate.review_bundle_id}/cover/candidates/"
            f"{candidate.id}/thumbnail"
        ),
    )


def get_asset_candidate_detail(
    session: Session, bundle_id: int, candidate_id: int
) -> AssetCandidateDetail | None:
    candidate = session.scalar(
        select(AssetCandidate).where(
            AssetCandidate.id == candidate_id,
            AssetCandidate.review_bundle_id == bundle_id,
        )
    )
    return _asset_candidate_detail(candidate) if candidate is not None else None


def plan_task_attempt(
    session: Session,
    bundle_id: int,
    *,
    kind: str,
    item_key: str,
    job_id: int,
) -> TaskAttempt:
    """Persist pending work before its job becomes visible to a worker."""
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    if BundleState(bundle.state) not in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
    }:
        raise ReviewInvariantError("review bundle is not open for enrichment")
    current = _current_revision(session, bundle_id)
    if current is None:
        raise ReviewInvariantError("review bundle has no current revision")
    latest = session.scalar(
        select(TaskAttempt)
        .where(
            TaskAttempt.review_bundle_id == bundle_id,
            TaskAttempt.kind == kind,
            TaskAttempt.item_key == item_key,
        )
        .order_by(TaskAttempt.attempt_no.desc())
        .limit(1)
    )
    if latest is not None and latest.state in {"pending", "running"}:
        if latest.job_id == job_id:
            return latest
        raise ReviewInvariantError(f"{kind} task for {item_key} is already active")
    attempt = TaskAttempt(
        review_bundle_id=bundle_id,
        proposal_revision_id=current.id,
        job_id=job_id,
        kind=kind,
        item_key=item_key,
        attempt_no=(latest.attempt_no if latest is not None else 0) + 1,
        state="pending",
    )
    session.add(attempt)
    session.flush()
    return attempt


def start_task_attempt(
    session: Session,
    bundle_id: int,
    *,
    kind: str,
    item_key: str,
    job_id: int | None = None,
) -> TaskAttempt:
    """Record a technical attempt without changing the review identity.

    Retries deliberately get a new ``attempt_no`` under the same bundle.  This
    lets a section fail or be retried while the user keeps the same open review.
    """
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    if BundleState(bundle.state) not in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
    }:
        raise ReviewInvariantError("review bundle is not open for enrichment")
    current = _current_revision(session, bundle_id)
    if current is None:
        raise ReviewInvariantError("review bundle has no current revision")
    planned = None
    if job_id is not None:
        planned = session.scalar(
            select(TaskAttempt).where(
                TaskAttempt.review_bundle_id == bundle_id,
                TaskAttempt.kind == kind,
                TaskAttempt.item_key == item_key,
                TaskAttempt.job_id == job_id,
                TaskAttempt.state == "pending",
            )
        )
    if planned is not None:
        planned.state = "running"
        session.flush()
        return planned
    previous = session.scalar(
        select(func.max(TaskAttempt.attempt_no)).where(
            TaskAttempt.review_bundle_id == bundle_id,
            TaskAttempt.kind == kind,
            TaskAttempt.item_key == item_key,
        )
    )
    attempt = TaskAttempt(
        review_bundle_id=bundle_id,
        proposal_revision_id=current.id,
        job_id=job_id,
        kind=kind,
        item_key=item_key,
        attempt_no=(previous or 0) + 1,
        state="running",
    )
    session.add(attempt)
    session.flush()
    return attempt


def _refresh_bundle_task_state(session: Session, bundle: ReviewBundle) -> None:
    attempts = list(
        session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.review_bundle_id == bundle.id)
            .order_by(TaskAttempt.kind, TaskAttempt.item_key, TaskAttempt.attempt_no)
        )
    )
    latest: dict[tuple[str, str], TaskAttempt] = {}
    for item in attempts:
        latest[(item.kind, item.item_key)] = item
    failures = [
        item for item in latest.values() if item.state in {"transient_failure", "permanent_failure"}
    ]
    if failures:
        bundle.state = BundleState.NEEDS_ATTENTION.value
        bundle.error = next((item.error for item in failures if item.error), "optional task failed")
    elif BundleState(bundle.state) in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
    }:
        bundle.state = BundleState.READY.value
        bundle.error = None


def finish_task_attempt(
    session: Session,
    attempt: TaskAttempt,
    *,
    state: str,
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> None:
    if state not in {
        "succeeded",
        "not_found",
        "transient_failure",
        "permanent_failure",
        "cancelled",
    }:
        raise ReviewInvariantError(f"unsupported task attempt state: {state!r}")
    attempt.state = state
    attempt.result = cast(dict[str, object], _json_copy(result)) if result is not None else None
    attempt.error = error
    bundle = attempt.review_bundle
    _refresh_bundle_task_state(session, bundle)
    session.flush()


def put_revision(
    session: Session,
    *,
    logical_key: str,
    title: str,
    scope_type: str,
    scope_id: int | None,
    source_snapshot: dict[str, object],
    operations: tuple[OperationDraft, ...],
    candidate_source: str | None = None,
    candidate_ref: str | None = None,
) -> RevisionWrite:
    """Create or update one active inbox row, idempotently.

    Revision identity is the normalized content plus the current parent revision.  Thus
    repeated refresh/select requests converge, while a genuine A -> B -> A sequence is
    retained as three audit revisions.
    """
    if not logical_key.strip():
        raise ReviewInvariantError("logical_key must not be empty")
    if not operations:
        raise ReviewInvariantError("a proposal revision requires at least one operation")

    normalized_snapshot_value = _json_copy(source_snapshot)
    if not isinstance(normalized_snapshot_value, dict):
        raise ReviewInvariantError("source_snapshot must be a JSON object")
    normalized_snapshot = cast(dict[str, object], normalized_snapshot_value)
    normalized_operations = tuple(_normalize_operation(operation) for operation in operations)
    revision_content: dict[str, object] = {
        "source_snapshot": normalized_snapshot,
        "operations": normalized_operations,
        "candidate_source": candidate_source,
        "candidate_ref": candidate_ref,
    }
    revision_digest = _digest(revision_content)
    snapshot_digest = _digest(normalized_snapshot)

    now = datetime.now(UTC)
    inserted_bundle_id = session.scalar(
        sqlite_insert(ReviewBundle)
        .values(
            logical_key=logical_key,
            title=title,
            scope_type=scope_type,
            scope_id=scope_id,
            state=BundleState.PREPARING.value,
            error=None,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
        .returning(ReviewBundle.id)
    )
    created_bundle = inserted_bundle_id is not None
    bundle = _active_bundle(session, logical_key)
    if bundle is None:
        raise ReviewInvariantError("could not create or resolve the active review bundle")
    if bundle.scope_type != scope_type or bundle.scope_id != scope_id:
        raise ReviewInvariantError(
            f"logical_key {logical_key!r} already belongs to a different scope"
        )

    current = _current_revision(session, bundle.id)
    if current is not None and current.content_digest == revision_digest:
        return RevisionWrite(
            bundle_id=bundle.id,
            revision_id=current.id,
            created_bundle=created_bundle,
            created_revision=False,
        )
    if BundleState(bundle.state) is BundleState.APPLYING:
        raise ReviewInvariantError("cannot replace a revision while its bundle is applying")

    session.scalar(
        sqlite_insert(SourceSnapshot)
        .values(
            review_bundle_id=bundle.id,
            content_digest=snapshot_digest,
            payload=normalized_snapshot,
            created_at=now,
        )
        .on_conflict_do_nothing()
        .returning(SourceSnapshot.id)
    )
    snapshot = session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.review_bundle_id == bundle.id,
            SourceSnapshot.content_digest == snapshot_digest,
        )
    )
    if snapshot is None:
        raise ReviewInvariantError("could not create or resolve the source snapshot")

    parent_revision_no = current.revision_no if current is not None else 0
    inserted_revision_id = session.scalar(
        sqlite_insert(ProposalRevision)
        .values(
            review_bundle_id=bundle.id,
            source_snapshot_id=snapshot.id,
            revision_no=parent_revision_no + 1,
            parent_revision_no=parent_revision_no,
            content_digest=revision_digest,
            is_current=False,
            candidate_source=candidate_source,
            candidate_ref=candidate_ref,
            created_at=now,
        )
        .on_conflict_do_nothing()
        .returning(ProposalRevision.id)
    )
    revision = session.scalar(
        select(ProposalRevision).where(
            ProposalRevision.review_bundle_id == bundle.id,
            ProposalRevision.parent_revision_no == parent_revision_no,
            ProposalRevision.content_digest == revision_digest,
        )
    )
    if revision is None:
        raise ReviewInvariantError(
            "the current revision changed concurrently; retry against the new current revision"
        )

    created_revision = inserted_revision_id is not None
    if created_revision:
        carried_decisions: dict[str, list[str]] = {}
        if current is not None:
            for operation in current.operations:
                key = _canonical_json(_persisted_operation_content(operation))
                carried_decisions.setdefault(key, []).append(operation.decision)
        for seq, normalized in enumerate(normalized_operations):
            decisions = carried_decisions.get(_canonical_json(normalized), [])
            revision.operations.append(
                Operation(
                    source_snapshot_id=snapshot.id,
                    seq=seq,
                    kind=cast(str, normalized["kind"]),
                    field=cast(str, normalized["field"]),
                    target_type=cast(str, normalized["target_type"]),
                    target_id=cast(int, normalized["target_id"]),
                    current_value=normalized["current_value"],
                    proposed_value=normalized["proposed_value"],
                    decision=decisions.pop(0) if decisions else "pending",
                    provenance=cast(dict[str, object], normalized["provenance"]),
                    validation=cast(dict[str, object], normalized["validation"]),
                )
            )
        session.flush()

    session.execute(
        update(ProposalRevision)
        .where(
            ProposalRevision.review_bundle_id == bundle.id,
            ProposalRevision.is_current.is_(True),
            ProposalRevision.id != revision.id,
        )
        .values(is_current=False)
    )
    revision.is_current = True
    session.flush()
    return RevisionWrite(
        bundle_id=bundle.id,
        revision_id=revision.id,
        created_bundle=created_bundle,
        created_revision=created_revision,
    )


def transition_bundle(session: Session, bundle_id: int, target: BundleState) -> ReviewBundle:
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    current_state = BundleState(bundle.state)
    validate_transition(current_state, target)
    if (
        target in (BundleState.READY, BundleState.NEEDS_ATTENTION)
        and _current_revision(session, bundle_id) is None
    ):
        raise ReviewInvariantError("a review cannot become visible without a current revision")
    if target is BundleState.APPLYING:
        raise InvalidBundleTransition("start_apply_run owns the transition to applying")
    bundle.state = target.value
    session.flush()
    return bundle


def start_apply_run(session: Session, bundle_id: int, *, idempotency_key: str) -> ApplyRun:
    if not idempotency_key.strip():
        raise ReviewInvariantError("idempotency_key must not be empty")
    existing = session.scalar(
        select(ApplyRun).where(
            ApplyRun.review_bundle_id == bundle_id,
            ApplyRun.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing

    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    current_state = BundleState(bundle.state)
    validate_transition(current_state, BundleState.APPLYING)
    current_revision = _current_revision(session, bundle_id)
    if current_revision is None:
        raise ReviewInvariantError("review bundle has no current revision")

    session.flush()
    active_tasks = tuple(
        (attempt.kind, attempt.item_key, attempt.state)
        for attempt in session.scalars(
            select(TaskAttempt)
            .where(
                TaskAttempt.review_bundle_id == bundle_id,
                TaskAttempt.state.in_(("pending", "running")),
            )
            .order_by(TaskAttempt.kind, TaskAttempt.item_key, TaskAttempt.attempt_no)
        )
    )
    if active_tasks:
        raise ReviewTasksPendingError(active_tasks)
    accepted = list(
        session.scalars(
            select(Operation)
            .where(
                Operation.proposal_revision_id == current_revision.id,
                Operation.decision == "accepted",
            )
            .order_by(Operation.seq)
        )
    )
    if not accepted:
        raise NoAcceptedOperationsError("review has no accepted operations")

    snapshot_items: dict[int, dict[str, object]] = {}
    raw_items = current_revision.source_snapshot.payload.get("items", [])
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if (
                isinstance(raw_item, dict)
                and raw_item.get("source_type") == "track"
                and isinstance(raw_item.get("source_id"), int)
            ):
                snapshot_items[cast(int, raw_item["source_id"])] = cast(
                    dict[str, object], raw_item
                )
    operation_ids_by_track: dict[int, list[int]] = {}
    for operation in accepted:
        if operation.target_type == "track":
            operation_ids_by_track.setdefault(operation.target_id, []).append(operation.id)

    manifest: dict[str, object] = {
        "version": 1,
        "revision_digest": current_revision.content_digest,
        "source_snapshot_digest": current_revision.source_snapshot.content_digest,
        "operation_ids": [operation.id for operation in accepted],
        "files": [
            {
                "track_id": track_id,
                "source": _json_copy(snapshot_items.get(track_id, {})),
                "operation_ids": operation_ids,
                "state": "pending",
                "error": None,
                "change_set_ids": [],
            }
            for track_id, operation_ids in sorted(operation_ids_by_track.items())
        ],
    }
    now = datetime.now(UTC)
    inserted_run_id = session.scalar(
        sqlite_insert(ApplyRun)
        .values(
            review_bundle_id=bundle.id,
            proposal_revision_id=current_revision.id,
            idempotency_key=idempotency_key,
            state="pending",
            manifest=manifest,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_nothing()
        .returning(ApplyRun.id)
    )
    if inserted_run_id is None:
        existing = session.scalar(
            select(ApplyRun).where(
                ApplyRun.review_bundle_id == bundle_id,
                ApplyRun.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        raise ReviewInvariantError("review bundle already has an active apply run")

    run = session.get(ApplyRun, inserted_run_id)
    if run is None:  # pragma: no cover - same transaction inserted this primary key
        raise ReviewInvariantError("could not create apply run")
    for operation in accepted:
        run.operation_attempts.append(
            OperationAttempt(
                operation_id=operation.id,
                attempted_value=_json_copy(operation.proposed_value),
                state="pending",
            )
        )
    bundle.state = BundleState.APPLYING.value
    session.flush()
    return run
