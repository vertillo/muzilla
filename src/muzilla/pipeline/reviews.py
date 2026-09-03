"""Transactional persistence for stable ReviewBundles and immutable revisions.

ReviewBundle is the native review contract. Legacy ChangeSets continue to use
their existing read/apply path while supported consumers transition.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import (  # pyright: ignore[reportMissingImports]
    and_,
    case,
    func,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.sqlite import (
    insert as sqlite_insert,  # pyright: ignore[reportMissingImports]
)
from sqlalchemy.orm import Session  # pyright: ignore[reportMissingImports]
from sqlalchemy.sql.elements import ColumnElement  # pyright: ignore[reportMissingImports]

from muzilla.config.schema import PathsConfig
from muzilla.db.batching import batched
from muzilla.db.fts import encode_fts5_literal
from muzilla.db.models import (
    ApplyRun,
    AssetCandidate,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ReviewBundle,
    ReviewInboxEntry,
    SourceSnapshot,
    TaskAttempt,
    Track,
)
from muzilla.domain import fields as field_registry
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
    candidate_snapshot: dict[str, object] | None
    match_explanation: dict[str, object] | None
    confidence: float | None
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
    undo_expired: bool = False
    undo_expiry_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewUndoRunDetail:
    id: int
    source_apply_run_id: int
    state: str
    result: dict[str, object] | None
    error: str | None
    job_ids: tuple[int, ...]


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
    undo_runs: tuple[ReviewUndoRunDetail, ...]


@dataclass(frozen=True, slots=True)
class SourceFileSummary:
    source_id: int | None
    filename: str | None
    path: str | None
    format: str | None
    art_blob_id: int | None
    cover_thumbnail_url: str | None


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


@dataclass(frozen=True, slots=True)
class ReviewNeighbors:
    previous_id: int | None
    next_id: int | None
    next_unreviewed_id: int | None


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
    try:
        return json.loads(_canonical_json(value))
    except (json.JSONDecodeError, ReviewInvariantError) as exc:
        raise ReviewInvariantError("review content must be JSON-serializable") from exc


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
    elif kind is OperationKind.GROUPING_CORRECTION:
        _validate_grouping_correction(
            field=operation.field,
            target_type=operation.target_type,
            current_value=current_value,
            proposed_value=proposed_value,
            validation=operation.validation,
        )

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


def _validate_grouping_correction(
    *,
    field: str,
    target_type: str,
    current_value: object,
    proposed_value: object,
    validation: dict[str, object],
) -> None:
    """Keep resolver operations narrow even when a producer bypasses the HTTP API."""
    if field != "collection" or target_type != "track":
        raise ReviewInvariantError("grouping_correction must target a track collection")
    if not isinstance(current_value, dict) or not isinstance(current_value.get("group_id"), int):
        raise ReviewInvariantError("grouping_correction requires a current source group")
    if not isinstance(proposed_value, dict):
        raise ReviewInvariantError("grouping_correction proposed_value must be an object")
    action = proposed_value.get("action")
    source_group_id = proposed_value.get("source_group_id")
    if action not in {"confirm_collection", "treat_as_singleton", "move_to_collection"}:
        raise ReviewInvariantError("unsupported grouping correction action")
    if not isinstance(source_group_id, int) or source_group_id != current_value["group_id"]:
        raise ReviewInvariantError("grouping correction source group must match current value")
    if action == "treat_as_singleton" and not isinstance(proposed_value.get("singleton_key"), str):
        raise ReviewInvariantError("singleton grouping correction requires a stable key")
    if action == "move_to_collection" and not isinstance(proposed_value.get("to_group_id"), int):
        raise ReviewInvariantError("collection grouping correction requires a target group")
    if validation.get("compatible") != True or not isinstance(validation.get("preview"), dict):  # noqa: E712
        raise ReviewInvariantError("grouping correction requires a compatible preview")


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
        art_blob_id = raw.get("art_blob_id")
        safe_art_blob_id = art_blob_id if isinstance(art_blob_id, int) else None
        summaries.append(
            SourceFileSummary(
                source_id=source_id if isinstance(source_id, int) else None,
                filename=safe_filename,
                path=safe_path,
                format=suffix or None,
                art_blob_id=safe_art_blob_id,
                cover_thumbnail_url=(
                    f"/api/blobs/{safe_art_blob_id}?size=thumb" if safe_art_blob_id else None
                ),
            )
        )
    return tuple(summaries)


def _review_issues(bundle: ReviewBundle, revision: ProposalRevision) -> tuple[ReviewIssue, ...]:
    issues: list[ReviewIssue] = []
    for attempt in bundle.task_attempts:
        if attempt.state in {"transient_failure", "permanent_failure"}:
            issues.append(
                ReviewIssue(
                    kind="task",
                    message=attempt.error or f"{attempt.kind} could not be completed",
                )
            )
    # Task state is the primary evidence for a task-originated ``bundle.error``.
    # Do not replace it with a duplicate generic review issue in the one-row inbox
    # projection, otherwise the task filter and label become misleading.
    if bundle.error and not any(item.message == bundle.error for item in issues):
        issues.insert(0, ReviewIssue(kind="review", message=bundle.error))
    for operation in revision.operations:
        errors_raw = operation.validation.get("errors")
        has_collision = bool(operation.validation.get("collision"))
        has_errors = isinstance(errors_raw, list) and len(errors_raw) > 0
        if has_collision:
            collision_path = operation.validation.get("collision_path")
            conflicting = operation.validation.get("conflicting_track_ids")
            if not isinstance(conflicting, list):
                conflicting = operation.validation.get("conflicting_ids", [])
            parts: list[str] = []
            if isinstance(collision_path, str) and collision_path:
                parts.append(f"Destination collision: {collision_path!r}")
            else:
                parts.append("Path collision")
            if isinstance(conflicting, list) and conflicting:
                try:
                    ids = sorted(int(x) for x in conflicting if isinstance(x, int))
                except Exception:
                    ids = []
                if ids:
                    parts.append(f"conflicts with track(s) {ids}")
            if has_errors:
                errors_list = errors_raw if isinstance(errors_raw, list) else []
                parts.append("; ".join(str(e) for e in errors_list))
            issues.append(ReviewIssue(kind="collision", message=" — ".join(parts)))
        elif has_errors:
            errors_list = errors_raw if isinstance(errors_raw, list) else []
            message = "; ".join(str(e) for e in errors_list)
            issues.append(ReviewIssue(kind="review", message=message))
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


def _confidence_label_for_revision(bundle: ReviewBundle, revision: ProposalRevision) -> str:
    if revision.candidate_snapshot is not None and revision.confidence is None:
        return "Manual selection"
    if revision.confidence is not None:
        if revision.confidence >= 0.85:
            return "High confidence"
        if revision.confidence >= 0.65:
            return "Medium confidence"
        return "Low confidence"
    return _confidence_label(bundle)


def _snapshot_text(snapshot: dict[str, object] | None, key: str) -> str | None:
    if not isinstance(snapshot, dict):
        return None
    value = snapshot.get(key)
    return value if isinstance(value, str) else None


def refresh_inbox_entry(session: Session, bundle_id: int) -> None:
    """Upsert the disposable inbox projection in the writer transaction.

    The projection can be rebuilt from immutable revisions, but normal mutations call
    this before commit so the inbox never presents a stale candidate/state after a
    restart.  FTS is maintained by SQLite triggers on this table.
    """
    session.flush()
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        return
    revision = _current_revision(session, bundle_id)
    if revision is None:
        return
    source_items = _source_file_summaries(revision)
    source = source_items[0] if source_items else None
    counts = {"accepted": 0, "pending": 0, "rejected": 0}
    for operation in revision.operations:
        counts[operation.decision] = counts.get(operation.decision, 0) + 1
    issues = _review_issues(bundle, revision)
    issue = issues[0] if issues else None
    snapshot = revision.candidate_snapshot
    values = {
        "review_bundle_id": bundle.id,
        "state": bundle.state,
        "title": bundle.title,
        "logical_key": bundle.logical_key,
        "updated_at": bundle.updated_at,
        "filename": source.filename if source else None,
        "path": source.path if source else None,
        "format": source.format if source else None,
        "candidate_source": revision.candidate_source,
        "candidate_ref": revision.candidate_ref,
        "candidate_title": _snapshot_text(snapshot, "title"),
        "candidate_artist": _snapshot_text(snapshot, "artist"),
        "candidate_album": _snapshot_text(snapshot, "album"),
        "confidence": revision.confidence,
        "confidence_label": _confidence_label_for_revision(bundle, revision),
        "issue_kind": issue.kind if issue else None,
        "issue_message": issue.message if issue else None,
        "accepted_operations": counts["accepted"],
        "pending_operations": counts["pending"],
        "rejected_operations": counts["rejected"],
    }
    session.execute(
        sqlite_insert(ReviewInboxEntry)
        .values(**values)
        .on_conflict_do_update(
            index_elements=[ReviewInboxEntry.review_bundle_id],
            set_={key: value for key, value in values.items() if key != "review_bundle_id"},
        )
    )


def _entry_to_summary(entry: ReviewInboxEntry) -> ReviewBundleSummary:
    return ReviewBundleSummary(
        id=entry.review_bundle_id,
        title=entry.title,
        state=entry.state,
        filename=entry.filename,
        path=entry.path,
        format=entry.format,
        candidate_source=entry.candidate_source,
        confidence=entry.confidence,
        confidence_label=entry.confidence_label,
        cover_thumbnail_url=None,
        issues=(
            (ReviewIssue(kind=entry.issue_kind, message=entry.issue_message),)
            if entry.issue_kind in {"review", "task", "collision"} and entry.issue_message
            else ()
        ),
        accepted_operations=entry.accepted_operations,
        pending_operations=entry.pending_operations,
        rejected_operations=entry.rejected_operations,
    )


def _inbox_query_parts(
    *,
    q: str | None,
    states: tuple[str, ...],
    confidence: str | None,
    issue: str | None,
    source: str | None,
    session_filter: str | None = None,
) -> tuple[ColumnElement[bool], ColumnElement[int], ColumnElement[int], dict[str, int]]:
    """Build the shared persisted-inbox predicate and deterministic sort expressions."""
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
    issue_rank = case((ReviewInboxEntry.issue_kind.is_not(None), 0), else_=1)
    rank = case(state_rank, value=ReviewInboxEntry.state, else_=99)
    filters: list[ColumnElement[bool]] = []
    if states:
        filters.append(ReviewInboxEntry.state.in_(states))
    else:
        filters.append(
            ReviewInboxEntry.state.not_in((BundleState.APPLIED.value, BundleState.DISCARDED.value))
        )
    if source:
        filters.append(ReviewInboxEntry.candidate_source == source)
    if issue:
        filters.append(ReviewInboxEntry.issue_kind == issue)
    if confidence:
        # ponytail: case-insensitive match to tolerate snake_case vs Title Case drift
        normalized = confidence.replace("_", " ").strip().lower()
        filters.append(func.lower(ReviewInboxEntry.confidence_label) == normalized)
    if session_filter is not None and session_filter != "":
        # session filter references the owning import_session_id on ReviewBundle.
        # ReviewInboxEntry is a projection; filter via correlated subquery to avoid schema migration.
        if session_filter == "none":
            filters.append(
                ReviewInboxEntry.review_bundle_id.in_(
                    select(ReviewBundle.id).where(ReviewBundle.import_session_id.is_(None))
                )
            )
        else:
            try:
                session_id = int(session_filter)
            except ValueError as exc:
                raise ReviewInvariantError(f"invalid session filter: {session_filter!r}") from exc
            filters.append(
                ReviewInboxEntry.review_bundle_id.in_(
                    select(ReviewBundle.id).where(ReviewBundle.import_session_id == session_id)
                )
            )
    literal = encode_fts5_literal(q)
    if literal:
        filters.append(
            ReviewInboxEntry.review_bundle_id.in_(
                select(text("rowid"))
                .select_from(text("review_inbox_entries_fts"))
                .where(text("review_inbox_entries_fts MATCH :match"))
                .params(match=literal)
            )
        )
    return and_(*filters), issue_rank, rank, state_rank


def list_review_bundles(
    db_session: Session,
    *,
    q: str | None = None,
    states: tuple[str, ...] = (),
    confidence: str | None = None,
    issue: str | None = None,
    source: str | None = None,
    session_filter: str | None = None,
    cursor: str | None = None,
    limit: int = 100,
) -> ReviewBundlePage:
    """Keyset query over the persisted inbox projection (never Python-filtered)."""
    condition, issue_rank, rank, state_rank = _inbox_query_parts(
        q=q,
        states=states,
        confidence=confidence,
        issue=issue,
        source=source,
        session_filter=session_filter,
    )
    total = (
        db_session.scalar(select(func.count()).select_from(ReviewInboxEntry).where(condition)) or 0
    )
    stmt = select(ReviewInboxEntry).where(condition)
    if cursor:
        try:
            after = tuple(int(part) for part in cursor.split(":", 2))
        except ValueError as exc:
            raise ReviewInvariantError("invalid review cursor") from exc
        if len(after) != 3:
            raise ReviewInvariantError("invalid review cursor")
        stmt = stmt.where(
            or_(
                issue_rank > after[0],
                and_(issue_rank == after[0], rank > after[1]),
                and_(
                    issue_rank == after[0],
                    rank == after[1],
                    ReviewInboxEntry.review_bundle_id > after[2],
                ),
            )
        )
    rows = list(
        db_session.scalars(
            stmt.order_by(issue_rank, rank, ReviewInboxEntry.review_bundle_id).limit(limit + 1)
        )
    )
    has_more = len(rows) > limit
    entries = rows[:limit]
    page_items = [_entry_to_summary(entry) for entry in entries]
    next_cursor = None
    if has_more and entries:
        last = entries[-1]
        next_cursor = f"{0 if last.issue_kind else 1}:{state_rank.get(last.state, 99)}:{last.review_bundle_id}"
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

    # Compute undo expiry per apply run (fail-closed when retention pruned journals)
    def _undo_expiry_for(run: ApplyRun) -> tuple[bool, str | None]:
        if run.state not in ("applied", "partially_applied"):
            return False, None
        # pending/applying/recovery_required journals are never pruned; check only terminal applied runs
        from muzilla.db.models import Operation as _OpExp
        from muzilla.db.models import ReviewFileJournal as _RFJExp

        has_applied_attempt = any(a.state == "applied" for a in run.operation_attempts)
        if not has_applied_attempt:
            return False, None
        journal_rows = list(session.scalars(select(_RFJExp).where(_RFJExp.apply_run_id == run.id)))
        if not journal_rows:
            return True, "expired — journal retention window elapsed (age or count threshold)"
        # Partial prune check: every applied operation's track should have a journal
        applied_tids: set[int] = set()
        for att in run.operation_attempts:
            if att.state == "applied":
                op = session.get(_OpExp, att.operation_id)
                if op is not None and op.target_type == "track":
                    applied_tids.add(op.target_id)
        if applied_tids:
            journal_tids = {j.track_id for j in journal_rows}
            if not journal_tids.issuperset(applied_tids):
                missing = sorted(applied_tids - journal_tids)
                return (
                    True,
                    f"expired — missing journals for track(s) {missing} (age or count threshold)",
                )
        return False, None

    apply_runs_list: list[ApplyRunDetail] = []
    for run in bundle.apply_runs:
        expired, reason = _undo_expiry_for(run)
        apply_runs_list.append(
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
                undo_expired=expired,
                undo_expiry_reason=reason,
            )
        )
    apply_runs = tuple(apply_runs_list)

    def undo_job_ids(run_manifest: dict[str, object]) -> tuple[int, ...]:
        raw_ids = run_manifest.get("job_ids", [])
        if not isinstance(raw_ids, list):
            return ()
        return tuple(value for value in raw_ids if isinstance(value, int))

    undo_runs = tuple(
        ReviewUndoRunDetail(
            id=run.id,
            source_apply_run_id=run.source_apply_run_id,
            state=run.state,
            result=run.result,
            error=run.error,
            job_ids=undo_job_ids(run.manifest),
        )
        for run in sorted(bundle.undo_runs, key=lambda item: item.id)
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
            candidate_snapshot=revision.candidate_snapshot,
            match_explanation=revision.match_explanation,
            confidence=revision.confidence,
            created_at=revision.created_at,
            operations=operations,
        ),
        source_items=_source_file_summaries(revision),
        cover_candidates=cover_candidates,
        task_attempts=task_attempts,
        apply_runs=apply_runs,
        undo_runs=undo_runs,
    )


def review_neighbors(
    db_session: Session,
    bundle_id: int,
    *,
    q: str | None = None,
    states: tuple[str, ...] = (),
    confidence: str | None = None,
    issue: str | None = None,
    source: str | None = None,
    session_filter: str | None = None,
) -> ReviewNeighbors:
    """Resolve navigation against the same persisted inbox order, not a UI page."""
    condition, issue_rank, rank, state_rank = _inbox_query_parts(
        q=q,
        states=states,
        confidence=confidence,
        issue=issue,
        source=source,
        session_filter=session_filter,
    )
    entry = db_session.scalar(
        select(ReviewInboxEntry).where(
            and_(condition, ReviewInboxEntry.review_bundle_id == bundle_id)
        )
    )
    if entry is None:
        raise ReviewInvariantError("review is not in the selected inbox")
    issue_value = 0 if entry.issue_kind else 1
    rank_value = state_rank.get(entry.state, 99)
    before = or_(
        issue_rank < issue_value,
        and_(issue_rank == issue_value, rank < rank_value),
        and_(
            issue_rank == issue_value,
            rank == rank_value,
            ReviewInboxEntry.review_bundle_id < bundle_id,
        ),
    )
    after = or_(
        issue_rank > issue_value,
        and_(issue_rank == issue_value, rank > rank_value),
        and_(
            issue_rank == issue_value,
            rank == rank_value,
            ReviewInboxEntry.review_bundle_id > bundle_id,
        ),
    )
    previous_id = db_session.scalar(
        select(ReviewInboxEntry.review_bundle_id)
        .where(and_(condition, before))
        .order_by(issue_rank.desc(), rank.desc(), ReviewInboxEntry.review_bundle_id.desc())
        .limit(1)
    )
    next_id = db_session.scalar(
        select(ReviewInboxEntry.review_bundle_id)
        .where(and_(condition, after))
        .order_by(issue_rank, rank, ReviewInboxEntry.review_bundle_id)
        .limit(1)
    )
    next_unreviewed_id = db_session.scalar(
        select(ReviewInboxEntry.review_bundle_id)
        .where(
            and_(
                condition,
                after,
                ReviewInboxEntry.state.in_(
                    (BundleState.READY.value, BundleState.NEEDS_ATTENTION.value)
                ),
            )
        )
        .order_by(issue_rank, rank, ReviewInboxEntry.review_bundle_id)
        .limit(1)
    )
    return ReviewNeighbors(previous_id, next_id, next_unreviewed_id)


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
    visible conflict instead of a write to an older immutable revision.
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
        session.scalars(select(Operation.id).where(Operation.proposal_revision_id == revision_id))
    )
    if not operation_ids <= persisted_ids:
        raise ReviewInvariantError("operation is not in the requested review revision")
    current = _current_revision(session, bundle_id)
    if current is None or current.id != revision_id:
        raise ReviewInvariantError("review revision changed; reload and retry")

    revision_operations = list(
        session.scalars(select(Operation).where(Operation.proposal_revision_id == revision_id))
    )
    requested_decisions = dict(decisions)
    accepted_grouping_by_track: dict[int, int] = {}
    for operation in revision_operations:
        next_decision = requested_decisions.get(operation.id, operation.decision)
        if operation.kind != OperationKind.GROUPING_CORRECTION.value or next_decision != "accepted":
            continue
        if operation.validation.get("compatible") != True:  # noqa: E712
            raise ReviewInvariantError("incompatible grouping correction cannot be accepted")
        accepted_grouping_by_track[operation.target_id] = (
            accepted_grouping_by_track.get(operation.target_id, 0) + 1
        )
    if any(count > 1 for count in accepted_grouping_by_track.values()):
        raise ReviewInvariantError("choose only one grouping correction for each track")

    current_revision_id = (
        select(ProposalRevision.id)
        .where(
            ProposalRevision.review_bundle_id == bundle_id,
            ProposalRevision.is_current.is_(True),
        )
        .scalar_subquery()
    )
    if bundle.state == BundleState.DISCARDED.value:
        if (
            session.scalar(
                select(ProposalRevision.id).where(ProposalRevision.id == current_revision_id)
            )
            != revision_id
        ):
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
    refresh_inbox_entry(session, bundle_id)
    detail = get_review_bundle(session, bundle_id)
    assert detail is not None
    return detail


_LRC_TIMESTAMP = re.compile(r"^\[(?:\d{1,2}):[0-5]\d(?:\.\d{1,3})?\]")


def _validate_manual_tag_value(field: str, value: object | None) -> None:
    try:
        definition = field_registry.get(field)
    except KeyError as exc:
        raise ReviewInvariantError(f"unknown metadata field: {field!r}") from exc
    if not definition.editable:
        raise ReviewInvariantError(f"metadata field {field!r} is not editable")
    if value is None:
        return
    if definition.type is field_registry.FieldType.TEXT and not isinstance(value, str):
        raise ReviewInvariantError(f"metadata field {field!r} requires text")
    if definition.type is field_registry.FieldType.INT and (
        not isinstance(value, int) or isinstance(value, bool)
    ):
        raise ReviewInvariantError(f"metadata field {field!r} requires an integer")
    if definition.type is field_registry.FieldType.FLOAT and (
        not isinstance(value, int | float) or isinstance(value, bool)
    ):
        raise ReviewInvariantError(f"metadata field {field!r} requires a number")
    if definition.type is field_registry.FieldType.BOOL and not isinstance(value, bool):
        raise ReviewInvariantError(f"metadata field {field!r} requires true or false")
    if definition.type is field_registry.FieldType.MULTI_TEXT and (
        not isinstance(value, list | tuple) or not all(isinstance(item, str) for item in value)
    ):
        raise ReviewInvariantError(f"metadata field {field!r} requires a list of text values")
    if definition.type is field_registry.FieldType.DATE:
        if not isinstance(value, str):
            raise ReviewInvariantError(f"metadata field {field!r} requires an ISO date")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ReviewInvariantError(f"metadata field {field!r} requires an ISO date") from exc


def _manual_lyrics_value(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ReviewInvariantError("write_lyrics edit requires text and synced")
    text_value = value.get("text")
    synced = value.get("synced")
    if not isinstance(text_value, str) or not isinstance(synced, bool):
        raise ReviewInvariantError("write_lyrics edit requires text and synced")
    if synced:
        lyric_lines = [line for line in text_value.splitlines() if line.strip()]
        if not lyric_lines or not all(_LRC_TIMESTAMP.match(line) for line in lyric_lines):
            raise ReviewInvariantError("synced lyrics require an LRC timestamp on every lyric line")
    return {"text": text_value, "synced": synced, "provider": "manual"}


def _effective_paths_config_for_session(session: Session) -> PathsConfig:
    """PATH-COLLISION-001: reload effective paths config for move preview."""
    try:
        from muzilla.config.loader import load_config
        from muzilla.pipeline.effective_settings import effective_paths_config

        base = load_config().paths
        return effective_paths_config(session, base)
    except Exception:
        return PathsConfig()


def _recompute_move_drafts(
    session: Session,
    bundle: ReviewBundle,
    metadata_drafts: list[OperationDraft],
) -> tuple[OperationDraft, ...]:
    """Recompute MOVE_FILE drafts from current metadata preview (no stale copy)."""
    from muzilla.db.models import Track, TrackGroup
    from muzilla.pipeline import paths as paths_service

    tracks: list[Track] = []
    if bundle.scope_type == "track" and bundle.scope_id is not None:
        track = session.get(Track, bundle.scope_id)
        if track is not None:
            tracks = [track]
    elif bundle.scope_type == "group" and bundle.scope_id is not None:
        group = session.get(TrackGroup, bundle.scope_id)
        if group is not None:
            tracks = list(group.tracks)
    if not tracks:
        return ()
    proposed_by_track: dict[int, dict[str, object]] = {track.id: {} for track in tracks}
    for draft in metadata_drafts:
        if draft.kind == OperationKind.SET_TAG.value:
            proposed_by_track.setdefault(draft.target_id, {})[draft.field] = draft.proposed_value
    config = _effective_paths_config_for_session(session)
    rows = paths_service.preview_rename(
        session,
        track_ids=[track.id for track in tracks],
        config=config,
        proposed_values_by_track_id=proposed_by_track,
    )
    # Map conflicting ids to their current file paths for accessible UI rendering
    conflicting_ids = {cid for r in rows for cid in r.conflicting_track_ids}
    path_by_id: dict[int, str] = {}
    if conflicting_ids:
        for batch in batched(list(conflicting_ids)):
            for tid, tpath in session.execute(
                select(Track.id, Track.path).where(Track.id.in_(batch))
            ):
                path_by_id[int(tid)] = str(tpath)
    return tuple(
        OperationDraft(
            kind=OperationKind.MOVE_FILE,
            field="path",
            target_type="track",
            target_id=row.track_id,
            current_value=row.old_path,
            proposed_value=row.new_path,
            provenance={"section": "path", "template": config.default},
            validation={
                "errors": list(row.errors),
                "collision": row.is_collision,
                "conflicting_track_ids": list(row.conflicting_track_ids)
                if row.is_collision
                else [],
                "conflicting_paths": [path_by_id.get(cid, "") for cid in row.conflicting_track_ids]
                if row.is_collision
                else [],
                "collision_path": row.collision_path,
            },
        )
        for row in rows
        if row.new_path != row.old_path
    )


def edit_operation(
    session: Session,
    bundle_id: int,
    *,
    operation_id: int,
    revision_id: int,
    kind: str,
    value: object | None,
) -> ReviewBundleDetail:
    """Create a successor revision for one typed manual edit.

    The original proposal remains immutable.  Unchanged operations are passed through
    ``put_revision`` so its stable-content matching retains their prior decisions;
    the edited operation is explicitly accepted in the successor.
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
    current = _current_revision(session, bundle_id)
    if current is None or current.id != revision_id:
        raise ReviewInvariantError("review revision changed; reload and retry")
    operation = session.get(Operation, operation_id)
    if operation is None or operation.proposal_revision_id != current.id:
        raise ReviewInvariantError("operation is not in the requested review revision")
    if operation.kind != kind or kind not in {"set_tag", "write_lyrics"}:
        raise ReviewInvariantError("only metadata tags and lyrics can be edited manually")

    edited_value: object | None
    section: str
    if kind == "set_tag":
        _validate_manual_tag_value(operation.field, value)
        edited_value = value
        section = "metadata"
    else:
        edited_value = _manual_lyrics_value(value)
        section = "lyrics"

    drafts: list[OperationDraft] = []
    edited_index = -1
    for index, existing in enumerate(current.operations):
        provenance = dict(existing.provenance)
        proposed_value = existing.proposed_value
        if existing.id == operation_id:
            provenance.update({"source": "manual", "section": section})
            proposed_value = edited_value
            edited_index = index
        drafts.append(
            OperationDraft(
                kind=existing.kind,
                field=existing.field,
                target_type=existing.target_type,
                target_id=existing.target_id,
                current_value=existing.current_value,
                proposed_value=proposed_value,
                provenance=provenance,
                validation=existing.validation,
            )
        )
    if edited_index < 0:  # defensive: relationship/load ordering changed unexpectedly
        raise ReviewInvariantError("operation is not in the requested review revision")

    # PATH-COLLISION-001: when metadata changes, recompute move preview (no stale copy)
    if kind == "set_tag":
        metadata_drafts = [d for d in drafts if d.kind == OperationKind.SET_TAG.value]
        non_move_drafts = [
            d
            for d in drafts
            if d.kind not in {OperationKind.SET_TAG.value, OperationKind.MOVE_FILE.value}
        ]
        new_moves = _recompute_move_drafts(session, bundle, metadata_drafts)
        drafts = metadata_drafts + list(new_moves) + non_move_drafts
        # edited operation remains a SET_TAG; locate its new index
        edited_index = next(
            (
                i
                for i, d in enumerate(drafts)
                if d.kind == OperationKind.SET_TAG.value
                and d.field == operation.field
                and d.target_id == operation.target_id
            ),
            -1,
        )

    write = put_revision(
        session,
        bundle_id=bundle_id,
        logical_key=bundle.logical_key,
        title=bundle.title,
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        source_snapshot=current.source_snapshot.payload,
        operations=tuple(drafts),
        candidate_source=current.candidate_source,
        candidate_ref=current.candidate_ref,
        candidate_snapshot=current.candidate_snapshot,
        match_explanation=current.match_explanation,
        confidence=current.confidence,
    )
    successor = _current_revision(session, bundle_id)
    if successor is None or successor.id != write.revision_id:
        raise ReviewInvariantError("review revision changed; reload and retry")
    successor_operations = sorted(successor.operations, key=lambda item: item.seq)
    if 0 <= edited_index < len(successor_operations):
        # editor's own SET_TAG is accepted; after move recompute its position may have shifted
        # find by field/target to be safe
        target = next(
            (
                op
                for op in successor_operations
                if op.kind == OperationKind.SET_TAG.value
                and op.field == operation.field
                and op.target_id == operation.target_id
            ),
            None,
        )
        if target is not None:
            target.decision = "accepted"
        else:
            successor_operations[edited_index].decision = "accepted"
    else:
        successor_operations[edited_index].decision = "accepted"
    if bundle.state == BundleState.DISCARDED.value:
        transition_bundle(session, bundle_id, BundleState.READY)
    session.flush()
    refresh_inbox_entry(session, bundle_id)
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
            f"/api/reviews/{candidate.review_bundle_id}/cover/candidates/{candidate.id}/thumbnail"
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
    refresh_inbox_entry(session, bundle_id)
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
        refresh_inbox_entry(session, bundle_id)
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
    refresh_inbox_entry(session, bundle_id)
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
    persisted_state = session.scalar(select(ReviewBundle.state).where(ReviewBundle.id == bundle.id))
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
    refresh_inbox_entry(session, bundle.id)


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
    candidate_snapshot: dict[str, object] | None = None,
    match_explanation: dict[str, object] | None = None,
    confidence: float | None = None,
) -> RevisionWrite:
    """Create or update one active inbox row, idempotently.

    Revision identity is the normalized content plus the current parent revision.  Thus
    repeated refresh/select requests converge, while a genuine A -> B -> A sequence is
    retained as three audit revisions.
    """
    if not logical_key.strip():
        raise ReviewInvariantError("logical_key must not be empty")
    if confidence is not None and not 0 <= confidence <= 1:
        raise ReviewInvariantError("confidence must be between 0 and 1")

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
        "candidate_snapshot": _json_copy(candidate_snapshot),
        "match_explanation": _json_copy(match_explanation),
        "confidence": confidence,
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
            candidate_snapshot=revision_content["candidate_snapshot"],
            match_explanation=revision_content["match_explanation"],
            confidence=confidence,
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
    refresh_inbox_entry(session, bundle.id)
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
    refresh_inbox_entry(session, bundle_id)
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

    accepted_grouping = [
        operation
        for operation in accepted
        if operation.kind == OperationKind.GROUPING_CORRECTION.value
    ]
    if accepted_grouping:
        if len(accepted_grouping) != len(accepted):
            raise ReviewInvariantError(
                "grouping corrections cannot be mixed with file operations in one apply run"
            )
        by_track: dict[int, int] = {}
        for operation in accepted_grouping:
            by_track[operation.target_id] = by_track.get(operation.target_id, 0) + 1
        if any(count > 1 for count in by_track.values()):
            raise ReviewInvariantError("choose only one grouping correction for each track")

    snapshot_items: dict[int, dict[str, object]] = {}
    raw_items = current_revision.source_snapshot.payload.get("items", [])
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if (
                isinstance(raw_item, dict)
                and raw_item.get("source_type") == "track"
                and isinstance(raw_item.get("source_id"), int)
            ):
                snapshot_items[cast(int, raw_item["source_id"])] = cast(dict[str, object], raw_item)
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
    refresh_inbox_entry(session, bundle.id)
    return run


def skip_review_bundle(session: Session, bundle_id: int, *, revision_id: int) -> ReviewBundleDetail:
    """Explicit Skip / Leave unchanged — resolves the item without modifying files.

    Creates a successor revision with no operations and a distinct skipped marker.
    The bundle becomes DISCARDED with a visible Skipped message, is distinct from
    unresolved needs_attention, and never blocks Apply of other bundles (it has no
    pending operations and is not in a blocking state).
    """
    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    if BundleState(bundle.state) not in {
        BundleState.PREPARING,
        BundleState.READY,
        BundleState.NEEDS_ATTENTION,
    }:
        raise ReviewInvariantError("review bundle is not skippable in its current state")
    current = _current_revision(session, bundle_id)
    if current is None or current.id != revision_id:
        raise ReviewInvariantError("review revision changed; reload and retry")
    # Keep same source snapshot payload for audit trail
    source_payload = current.source_snapshot.payload
    if not isinstance(source_payload, dict):
        raise ReviewInvariantError("source snapshot payload is invalid")
    skipped_snapshot: dict[str, object] = {
        "confidence_band": "skipped",
        "resolution": "skipped",
        "band": "skipped",
    }
    skipped_explanation: dict[str, object] = {
        "outcome": "skipped",
        "band": "skipped",
        "resolution": "explicit_skip",
    }
    # Preserve provider outcomes if present for diagnostics
    if current.match_explanation and isinstance(current.match_explanation, dict):
        prov = current.match_explanation.get("provider_outcomes")
        if prov:
            skipped_explanation["provider_outcomes"] = prov
    put_revision(
        session,
        bundle_id=bundle.id,
        logical_key=bundle.logical_key,
        title=bundle.title,
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        source_snapshot=source_payload,
        operations=(),
        candidate_source=None,
        candidate_ref=None,
        candidate_snapshot=skipped_snapshot,
        match_explanation=skipped_explanation,
        confidence=None,
    )
    # Transition to DISCARDED with distinct skipped error
    # Use transition_bundle if possible; DISCARDED is reachable from these states
    try:
        transition_bundle(session, bundle.id, BundleState.DISCARDED)
    except InvalidBundleTransition:
        bundle.state = BundleState.DISCARDED.value  # fallback
        session.flush()
        refresh_inbox_entry(session, bundle.id)
    bundle.error = "Skipped \u2014 Leave unchanged"
    session.flush()
    refresh_inbox_entry(session, bundle.id)
    detail = get_review_bundle(session, bundle.id)
    assert detail is not None
    return detail


def refresh_review_bundle(session: Session, bundle_id: int) -> ReviewBundleDetail:
    """REVIEW-CONFLICTS-001: refresh source snapshot from current file facts."""
    from pathlib import Path

    from muzilla.db.models import Track

    bundle = session.get(ReviewBundle, bundle_id)
    if bundle is None:
        raise ReviewInvariantError(f"review bundle {bundle_id} not found")
    cur = _current_revision(session, bundle_id)
    if cur is None:
        raise ReviewInvariantError("review has no current revision to refresh")
    raw_items = cur.source_snapshot.payload.get("items", [])
    if not isinstance(raw_items, list):
        raise ReviewInvariantError("source snapshot has no items")
    new_items: list[dict[str, object]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        src_id = raw.get("source_id")
        if not isinstance(src_id, int):
            src_id = raw.get("track_id")
        if not isinstance(src_id, int):
            continue
        track = session.get(Track, src_id)
        if track is None:
            raise ReviewInvariantError(f"track {src_id} not found for refresh")
        # Re-read file facts
        path = Path(track.path)
        try:
            stat = path.stat()
        except OSError as exc:
            raise ReviewInvariantError(f"cannot stat track {src_id}: {exc}") from exc
        from muzilla.domain.metadata import tag_hash as compute_tag_hash  # local to avoid cycle
        from muzilla.tags.reader import read_track

        try:
            meta = read_track(path)
            th = compute_tag_hash(meta)
        except Exception:
            th = track.tag_hash or ""
        # Update track cached facts for future preflight
        track.size_bytes = stat.st_size
        track.mtime_ns = stat.st_mtime_ns
        track.tag_hash = th
        session.flush()
        new_items.append(
            {
                "source_type": raw.get("source_type", "track"),
                "source_id": src_id,
                "path": track.path,
                "size_bytes": track.size_bytes,
                "mtime_ns": track.mtime_ns,
                "tag_hash": track.tag_hash,
                "filename": track.filename,
            }
        )
    # Build new snapshot payload
    new_snapshot = cast(dict[str, object], {"items": new_items})
    # Preserve operations and candidate data, but recompute move preview (no stale collision)
    drafts: list[OperationDraft] = []
    for op in cur.operations:
        drafts.append(
            OperationDraft(
                kind=op.kind,
                field=op.field,
                target_type=op.target_type,
                target_id=op.target_id,
                current_value=op.current_value,
                proposed_value=op.proposed_value,
                provenance=dict(op.provenance),
                validation=dict(op.validation),
            )
        )
    # PATH-COLLISION-001: refresh must recalc collisions, not copy stale validation
    has_move = any(draft.kind == OperationKind.MOVE_FILE.value for draft in drafts)
    if has_move:
        metadata_drafts = [d for d in drafts if d.kind == OperationKind.SET_TAG.value]
        non_move = [
            d
            for d in drafts
            if d.kind not in {OperationKind.SET_TAG.value, OperationKind.MOVE_FILE.value}
        ]
        new_moves = _recompute_move_drafts(session, bundle, metadata_drafts)
        drafts = metadata_drafts + list(new_moves) + non_move
    _write = put_revision(
        session,
        bundle_id=bundle.id,
        logical_key=bundle.logical_key,
        title=bundle.title,
        scope_type=bundle.scope_type,
        scope_id=bundle.scope_id,
        source_snapshot=new_snapshot,
        operations=tuple(drafts),
        candidate_source=cur.candidate_source,
        candidate_ref=cur.candidate_ref,
        candidate_snapshot=cur.candidate_snapshot,
        match_explanation=cur.match_explanation,
        confidence=cur.confidence,
    )
    session.flush()
    detail = get_review_bundle(session, bundle.id)
    assert detail is not None
    return detail
