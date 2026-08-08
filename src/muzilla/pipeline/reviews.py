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
    source_items: tuple[SourceFileSummary, ...]
    cover_candidates: tuple[AssetCandidateDetail, ...]
    task_attempts: tuple[TaskAttemptDetail, ...]
    apply_runs: tuple[ApplyRunDetail, ...]


@dataclass(frozen=True, slots=True)
class SourceFileSummary:
    source_id: int | None
    filename: str | None
    path: str | None
    format: str | None


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    kind: str
    message: str


@dataclass(frozen=True, slots=True)
class ReviewBundleSummary:
    id: int
    title: str
    state: str
    filename: str | None
    path: str | None
    format: str | None
    candidate_source: str | None
    confidence: float | None
    confidence_label: str
    cover_thumbnail_url: str | None
    issues: tuple[ReviewIssue, ...]
    accepted_operations: int
    pending_operations: int
    rejected_operations: int


@dataclass(frozen=True, slots=True)
class ReviewBundlePage:
    items: tuple[ReviewBundleSummary, ...]
    next_cursor: str | None
    total: int


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


def _source_file_summaries(revision: ProposalRevision) -> tuple[SourceFileSummary, ...]:
    """Read only the immutable source identity stored with a revision.

    Older bundles may predate ``filename`` in the snapshot.  Those remain readable and
    are deliberately rendered as unknown rather than reaching into the mutable catalog.
    """
    raw_items = revision.source_snapshot.payload.get("items", [])
    if not isinstance(raw_items, list):
        return ()

    summaries: list[SourceFileSummary] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        source_id = raw.get("source_id")
        filename = raw.get("filename")
        path = raw.get("path")
        safe_filename = filename if isinstance(filename, str) else None
        safe_path = path if isinstance(path, str) else None
        extension_source = safe_filename or safe_path or ""
        suffix = extension_source.rsplit(".", 1)[-1].lower() if "." in extension_source else ""
        summaries.append(
            SourceFileSummary(
                source_id=source_id if isinstance(source_id, int) else None,
                filename=safe_filename,
                path=safe_path,
                format=suffix or None,
            )
        )
    return tuple(summaries)


def _review_issues(bundle: ReviewBundle, revision: ProposalRevision) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    if bundle.error:
        issues.append(ReviewIssue(kind="review", message=bundle.error))
    for attempt in bundle.task_attempts:
        if attempt.state in {"transient_failure", "permanent_failure"}:
            issues.append(
                ReviewIssue(
                    kind="task",
                    message=attempt.error or f"{attempt.kind} could not be completed",
                )
            )
    for operation in revision.operations:
        errors = operation.validation.get("errors")
        if operation.validation.get("collision") or (isinstance(errors, list) and errors):
            message = "; ".join(str(error) for error in errors) if isinstance(errors, list) else "Path collision"
            issues.append(ReviewIssue(kind="collision", message=message or "Path collision"))
    return tuple(issues)


def _confidence_label(bundle: ReviewBundle) -> str:
    # Matching confidence is intentionally not inferred from operation count or a
    # provider name.  Existing ReviewBundle revisions do not persist a score, so the
    # explicit absence prevents a misleading green percentage in the inbox.
    if bundle.state == BundleState.NEEDS_ATTENTION.value:
        return "Needs attention"
    if bundle.state == BundleState.PREPARING.value:
        return "Preparing"
    return "Not scored"


def _to_summary(bundle: ReviewBundle, revision: ProposalRevision) -> ReviewBundleSummary:
    source_items = _source_file_summaries(revision)
    first_source = source_items[0] if source_items else None
    counts = {"accepted": 0, "pending": 0, "rejected": 0}
    for operation in revision.operations:
        counts[operation.decision] = counts.get(operation.decision, 0) + 1
    return ReviewBundleSummary(
        id=bundle.id,
        title=bundle.title,
        state=bundle.state,
        filename=first_source.filename if first_source else None,
        path=first_source.path if first_source else None,
        format=first_source.format if first_source else None,
        candidate_source=revision.candidate_source,
        confidence=None,
        confidence_label=_confidence_label(bundle),
        cover_thumbnail_url=(
            _asset_candidate_detail(bundle.asset_candidates[0]).thumbnail_url
            if bundle.asset_candidates
            else None
        ),
        issues=_review_issues(bundle, revision),
        accepted_operations=counts["accepted"],
        pending_operations=counts["pending"],
        rejected_operations=counts["rejected"],
    )


def list_review_bundles(
    session: Session,
    *,
    q: str | None = None,
    states: tuple[str, ...] = (),
    confidence: str | None = None,
    issue: str | None = None,
    source: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ReviewBundlePage:
    """List current ReviewBundles for the inbox without exposing legacy ChangeSets.

    The source snapshot is a JSON payload and deliberately stays immutable; filtering
    it in Python keeps this transitional read adapter portable across SQLite builds.
    The endpoint is bounded, ordered deterministically, and can move to indexed columns
    without changing its UI contract when the library needs it.
    """
    rows = list(
        session.execute(
            select(ReviewBundle, ProposalRevision)
            .join(ProposalRevision, ProposalRevision.review_bundle_id == ReviewBundle.id)
            .where(ProposalRevision.is_current.is_(True))
        )
    )
    summaries = [_to_summary(bundle, revision) for bundle, revision in rows]

    normalized_query = (q or "").strip().casefold()
    requested_states = set(states)
    if requested_states:
        summaries = [summary for summary in summaries if summary.state in requested_states]
    else:
        # Archived and applied reviews remain available through the explicit state
        # filter, but must not remain in the default work queue.
        summaries = [
            summary
            for summary in summaries
            if summary.state not in {BundleState.APPLIED.value, BundleState.DISCARDED.value}
        ]
    if source:
        summaries = [summary for summary in summaries if summary.candidate_source == source]
    if issue:
        summaries = [
            summary
            for summary in summaries
            if any(item.kind == issue for item in summary.issues)
        ]
    if confidence:
        summaries = [
            summary
            for summary in summaries
            if summary.confidence_label.casefold().replace(" ", "_") == confidence.casefold()
        ]
    if normalized_query:
        def matches(summary: ReviewBundleSummary) -> bool:
            values = (
                summary.title,
                summary.filename,
                summary.path,
                summary.candidate_source,
                *(item.message for item in summary.issues),
                str(summary.id),
            )
            return any(normalized_query in value.casefold() for value in values if value)
        summaries = [summary for summary in summaries if matches(summary)]

    state_rank = {
        BundleState.NEEDS_ATTENTION.value: 0,
        BundleState.FAILED.value: 0,
        BundleState.PREPARING.value: 1,
        BundleState.READY.value: 2,
        BundleState.APPLYING.value: 3,
        BundleState.PARTIALLY_APPLIED.value: 4,
        BundleState.APPLIED.value: 5,
        BundleState.DISCARDED.value: 6,
    }
    def sort_key(summary: ReviewBundleSummary) -> tuple[int, int, int]:
        return (0 if summary.issues else 1, state_rank[summary.state], summary.id)

    summaries.sort(key=sort_key)
    total = len(summaries)
    if cursor:
        try:
            after = tuple(int(part) for part in cursor.split(":", 2))
        except ValueError as exc:
            raise ReviewInvariantError("invalid review cursor") from exc
        if len(after) != 3:
            raise ReviewInvariantError("invalid review cursor")
        summaries = [summary for summary in summaries if sort_key(summary) > after]
    page_items = summaries[:limit]
    next_cursor = None
    if len(summaries) > limit and page_items:
        next_cursor = ":".join(str(value) for value in sort_key(page_items[-1]))
    return ReviewBundlePage(items=tuple(page_items), next_cursor=next_cursor, total=total)


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
        source_items=_source_file_summaries(revision),
        cover_candidates=cover_candidates,
        task_attempts=task_attempts,
        apply_runs=apply_runs,
    )


def apply_operation_decisions(
    session: Session,
    bundle_id: int,
    *,
    revision_id: int,
    decisions: tuple[tuple[int, str], ...],
) -> ReviewBundleDetail:
    """Persist decisions only if the client still addresses the current revision.

    A producer may promote a successor while the browser is autosaving.  The update is
    conditioned in SQL on the revision still being current, so that race becomes a
    visible conflict instead of a write to a historical revision.
    """
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    if BundleState(bundle.state) not in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
        BundleState.DISCARDED,
    }:
        raise ReviewInvariantError(f"review bundle {bundle_id} is not editable")

    if not decisions:
        raise ReviewInvariantError("at least one review decision is required")
    operation_ids: set[int] = set()
    for operation_id, decision in decisions:
        if decision not in {"pending", "accepted", "rejected"}:
            raise ReviewInvariantError(f"unsupported review decision: {decision!r}")
        if operation_id in operation_ids:
            raise ReviewInvariantError(f"operation {operation_id} has multiple decisions")
        operation_ids.add(operation_id)

    persisted_ids = set(
        session.scalars(
            select(Operation.id).where(Operation.proposal_revision_id == revision_id)
        )
    )
    if not operation_ids <= persisted_ids:
        raise ReviewInvariantError("operation is not in the requested review revision")
    current = _current_revision(session, bundle_id)
    if current is None or current.id != revision_id:
        raise ReviewInvariantError("review revision changed; reload and retry")

    current_revision_id = (
        select(ProposalRevision.id)
        .where(
            ProposalRevision.review_bundle_id == bundle_id,
            ProposalRevision.is_current.is_(True),
        )
        .scalar_subquery()
    )
    if bundle.state == BundleState.DISCARDED.value:
        if session.scalar(select(ProposalRevision.id).where(ProposalRevision.id == current_revision_id)) != revision_id:
            raise ReviewInvariantError("review revision changed; reload and retry")
        # The database freezes decisions while a bundle is discarded.  Reopen first
        # in this transaction, then apply the requested decision atomically below.
        reopen_state = (
            BundleState.NEEDS_ATTENTION
            if any(
                attempt.state in {"transient_failure", "permanent_failure"}
                for attempt in _latest_task_attempts(session, bundle_id)
            )
            else BundleState.READY
        )
        transition_bundle(session, bundle_id, reopen_state)

    for operation_id, decision in decisions:
        updated_operation_id = session.scalar(
            update(Operation)
            .where(
                Operation.id == operation_id,
                Operation.proposal_revision_id == revision_id,
                Operation.proposal_revision_id == current_revision_id,
            )
            .values(decision=decision)
            .returning(Operation.id)
        )
        if updated_operation_id != operation_id:
            raise ReviewInvariantError("review revision changed; reload and retry")

    still_open = session.scalar(
        select(Operation.id)
        .where(
            Operation.proposal_revision_id == revision_id,
            Operation.decision != "rejected",
        )
        .limit(1)
    )
    if still_open is None:
        transition_bundle(session, bundle_id, BundleState.DISCARDED)
    session.flush()
    detail = get_review_bundle(session, bundle_id)
    assert detail is not None
    return detail


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


def _latest_task_attempts(session: Session, bundle_id: int) -> tuple[TaskAttempt, ...]:
    attempts = list(
        session.scalars(
            select(TaskAttempt)
            .where(TaskAttempt.review_bundle_id == bundle_id)
            .order_by(TaskAttempt.kind, TaskAttempt.item_key, TaskAttempt.attempt_no)
        )
    )
    latest: dict[tuple[str, str], TaskAttempt] = {}
    for item in attempts:
        latest[(item.kind, item.item_key)] = item
    return tuple(latest.values())


def _refresh_bundle_task_state(session: Session, bundle: ReviewBundle) -> None:
    persisted_state = session.scalar(
        select(ReviewBundle.state).where(ReviewBundle.id == bundle.id)
    )
    if persisted_state == BundleState.DISCARDED.value:
        # A user archive is authoritative over late optional-task outcomes.  Do
        # not use a stale worker-side ORM object to traverse the reversible
        # discarded -> needs_attention edge introduced for explicit edits.
        session.expire(bundle, ["state", "error"])
        return
    failures = [
        item
        for item in _latest_task_attempts(session, bundle.id)
        if item.state in {"transient_failure", "permanent_failure"}
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
    bundle_id: int | None = None,
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
    if bundle_id is None:
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
    else:
        bundle = session.get(ReviewBundle, bundle_id)
        if bundle is None:
            raise ReviewInvariantError(f"review bundle {bundle_id} not found")
        created_bundle = False
    if bundle.scope_type != scope_type or bundle.scope_id != scope_id:
        raise ReviewInvariantError(
            f"logical_key {logical_key!r} already belongs to a different scope"
        )
    if bundle.logical_key != logical_key:
        raise ReviewInvariantError(f"review bundle {bundle.id} has a different logical_key")

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
