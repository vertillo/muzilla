"""Safe catalog/factory reset with persistent lock, audit, and recovery.

This service owns only Muzilla's index, activity, cache, blob and managed provider
credential stores.  It never traverses ``library_root`` or ``backup_dir`` and it keeps
the migration marker, auth epoch, maintenance lock and audit rows across every scope.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from muzilla.config.schema import Config
from muzilla.db.models import (
    AdminOperation,
    ApplyJournal,
    ApplyRun,
    AssetCandidate,
    Blob,
    CandidateUrlAlias,
    Change,
    ChangeSet,
    DuplicateGroup,
    DuplicateMember,
    ImportSession,
    ImportTask,
    Job,
    JobEvent,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ProviderCache,
    ReviewBundle,
    ReviewUndoRun,
    Setting,
    SourceSnapshot,
    SystemState,
    TaskAttempt,
    Track,
    TrackFingerprintMatch,
    TrackGroup,
)
from muzilla.jobs import queue
from muzilla.services import auth_epoch
from muzilla.services.secrets import SecretStore


class ResetScope(StrEnum):
    CATALOG_AND_ACTIVITY = "catalog_and_activity"
    FACTORY = "factory"


class ResetError(RuntimeError):
    pass


class ResetIdempotencyConflict(ResetError):
    pass


class ResetInProgress(ResetError):
    pass


class UnsafeResetTarget(ResetError):
    pass


class ResetIncomplete(ResetError):
    """Authorized DB cleanup committed, but external cleanup must be resumed."""


@dataclass(frozen=True, slots=True)
class ResetResult:
    operation_id: int
    scope: str
    state: str
    settings_preserved: bool
    secrets_preserved: bool
    music_files_touched: bool
    deleted_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ResetTargets:
    cache: Path
    blob: Path
    secret: Path


_DELETE_ORDER = (
    ReviewUndoRun,
    OperationAttempt,
    ApplyRun,
    CandidateUrlAlias,
    TaskAttempt,
    AssetCandidate,
    Operation,
    ProposalRevision,
    SourceSnapshot,
    ReviewBundle,
    DuplicateMember,
    DuplicateGroup,
    TrackFingerprintMatch,
    ImportTask,
    ImportSession,
    JobEvent,
    Job,
    ApplyJournal,
    Change,
    ChangeSet,
    ProviderCache,
    Track,
    TrackGroup,
    Blob,
)


def _request_digest(scope: ResetScope) -> str:
    return hashlib.sha256(f"muzilla-reset-v1:{scope.value}".encode()).hexdigest()


def _system_state(session: Session) -> SystemState:
    state = session.get(SystemState, 1)
    if state is None:
        state = SystemState(id=1)
        session.add(state)
        session.flush()
    return state


def prepare_reset(
    session: Session, *, scope: ResetScope, idempotency_key: str
) -> AdminOperation:
    """Persist authorization intent and acquire the cross-process queue gate."""
    key = idempotency_key.strip()
    if not key or len(key) > 128:
        raise ResetError("Idempotency-Key must contain 1 to 128 characters")
    digest = _request_digest(scope)
    existing = session.scalar(
        select(AdminOperation).where(AdminOperation.idempotency_key == key)
    )
    if existing is not None:
        if existing.request_digest != digest or existing.scope != scope.value:
            raise ResetIdempotencyConflict(
                "Idempotency-Key was already used for a different reset scope"
            ) from None
        return existing

    state = _system_state(session)
    if state.maintenance_mode:
        raise ResetInProgress("another reset is already in progress")
    operation = AdminOperation(
        idempotency_key=key,
        scope=scope.value,
        request_digest=digest,
        state="running",
        phase="prepared",
        actor="single-user",
    )
    session.add(operation)
    session.flush()
    state.maintenance_mode = True
    state.active_operation_id = operation.id
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = session.scalar(
            select(AdminOperation).where(AdminOperation.idempotency_key == key)
        )
        if winner is None:
            raise
        if winner.request_digest != digest or winner.scope != scope.value:
            raise ResetIdempotencyConflict(
                "Idempotency-Key was already used for a different reset scope"
            ) from None
        return winner
    return operation


def request_worker_quiesce(session: Session) -> int:
    """Cancel all work at its existing cooperative safe boundaries."""
    active = list(
        session.scalars(select(Job).where(Job.state.in_(("pending", "running", "cancelling"))))
    )
    for job in active:
        queue.request_cancel(session, job.id)
    return len(active)


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _related(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _validate_directory_root(path: Path, *, label: str) -> Path:
    resolved = _resolved(path)
    broad_roots = {
        _resolved(candidate)
        for candidate in (
            Path(resolved.anchor),
            Path.home(),
            Path.home().parent,
            Path.cwd(),
            Path(tempfile.gettempdir()),
            Path("/Users"),
            Path("/home"),
            Path("/private/tmp"),
            Path("/tmp"),
            Path("/var"),
            Path("/usr"),
            Path("/opt"),
            Path("/srv"),
            Path("/mnt"),
            Path("/Volumes"),
        )
    }
    if resolved in broad_roots:
        raise UnsafeResetTarget(f"{label} reset target is too broad")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return resolved
    if stat.S_ISLNK(metadata.st_mode):
        raise UnsafeResetTarget(f"{label} reset target must not be a symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafeResetTarget(f"{label} reset target is not a directory")
    return resolved


def validate_reset_targets(config: Config) -> ResetTargets:
    """Fail before the first delete if any managed root could reach music/config."""
    library = _resolved(config.storage.library_root)
    database = _resolved(config.storage.db_path)
    secret = _validate_directory_root(
        config.storage.resolved_provider_secrets_dir(), label="provider secret"
    )
    cache = _validate_directory_root(config.storage.cache_dir, label="cache")
    blob = _validate_directory_root(config.storage.blob_dir, label="blob")
    backup = (
        _resolved(config.storage.backup_dir)
        if config.storage.backup_dir is not None
        else None
    )

    for label, target in (("cache", cache), ("blob", blob), ("provider secret", secret)):
        if _related(target, library):
            raise UnsafeResetTarget(f"{label} reset target overlaps the library")
        if database == target or database.is_relative_to(target):
            raise UnsafeResetTarget(f"{label} reset target contains the database")
        if backup is not None and _related(target, backup):
            raise UnsafeResetTarget(f"{label} reset target overlaps the backup directory")
    if _related(cache, blob) or _related(cache, secret) or _related(blob, secret):
        raise UnsafeResetTarget("cache, blob, and provider secret roots must be disjoint")
    return ResetTargets(cache=cache, blob=blob, secret=secret)


def _target_fingerprint(targets: ResetTargets) -> str:
    payload = "\0".join(str(path) for path in (targets.cache, targets.blob, targets.secret))
    return hashlib.sha256(f"muzilla-reset-targets-v1\0{payload}".encode()).hexdigest()


def workers_are_quiescent(session: Session) -> bool:
    """Return true only after every live lease has reached terminal state.

    The persistent maintenance gate prevents a recovered job from being leased again.
    Expired external leases can therefore use the queue's normal crash-recovery
    transition instead of blocking reset forever; an unexpired lease remains a strict
    barrier until its worker acknowledges cancellation or its lease expires.
    """
    session.expire_all()
    state = session.get(SystemState, 1)
    if state is not None and state.maintenance_mode:
        queue.recover_stuck_jobs(session)
        session.expire_all()
    active = session.scalar(
        select(Job.id).where(Job.state.in_(("pending", "running", "cancelling"))).limit(1)
    )
    return active is None


def maintenance_is_active(session: Session) -> bool:
    session.expire_all()
    state = session.get(SystemState, 1)
    return state is not None and state.maintenance_mode


def _require_workers_quiescent(session: Session) -> None:
    # Keep the service boundary safe even when recovery or a non-HTTP caller
    # invokes execution directly instead of issuing the cancellation first.
    request_worker_quiesce(session)
    if not workers_are_quiescent(session):
        raise ResetInProgress("worker quiesce is still in progress")


def _clear_directory_contents(root: Path) -> None:
    try:
        metadata = root.lstat()
    except FileNotFoundError:
        root.mkdir(parents=True)
        return
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise UnsafeResetTarget("managed reset target changed after validation")
    for child in root.iterdir():
        child_metadata = child.lstat()
        if stat.S_ISDIR(child_metadata.st_mode) and not stat.S_ISLNK(child_metadata.st_mode):
            shutil.rmtree(child)
        else:
            child.unlink()
    descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _result(operation: AdminOperation) -> ResetResult:
    counts = operation.outcome.get("deleted_counts", {})
    return ResetResult(
        operation_id=operation.id,
        scope=operation.scope,
        state=operation.state,
        settings_preserved=operation.scope == ResetScope.CATALOG_AND_ACTIVITY.value,
        secrets_preserved=operation.scope == ResetScope.CATALOG_AND_ACTIVITY.value,
        music_files_touched=False,
        deleted_counts={
            str(key): int(value)
            for key, value in counts.items()
            if isinstance(key, str) and isinstance(value, int)
        }
        if isinstance(counts, dict)
        else {},
    )


def _fail_before_delete(session: Session, operation: AdminOperation, error: Exception) -> None:
    operation.state = "failed"
    operation.phase = "validation_failed"
    operation.error = str(error)
    operation.outcome = {"status": "failed", "phase": "validation"}
    operation.completed_at = datetime.now(UTC)
    state = _system_state(session)
    state.maintenance_mode = False
    state.active_operation_id = None
    session.commit()


def execute_prepared_reset(
    session: Session,
    *,
    config: Config,
    secret_store: SecretStore,
    operation_id: int,
    defer_completion: bool = False,
) -> ResetResult:
    """Run/recover the allow-listed cleanup after the worker is quiescent."""
    operation = session.get(AdminOperation, operation_id)
    if operation is None:
        raise ResetError("reset audit record not found")
    if operation.state == "succeeded":
        return _result(operation)
    if operation.state == "failed":
        raise ResetError(operation.error or "reset previously failed")
    state = _system_state(session)
    if not state.maintenance_mode or state.active_operation_id != operation.id:
        raise ResetError("reset does not own the maintenance lock")

    _require_workers_quiescent(session)

    if operation.phase == "prepared":
        try:
            targets = validate_reset_targets(config)
        except UnsafeResetTarget as exc:
            _fail_before_delete(session, operation, exc)
            raise

        deleted_counts: dict[str, int] = {}
        for model in _DELETE_ORDER:
            result = session.execute(delete(model))
            deleted_counts[model.__tablename__] = getattr(result, "rowcount", 0) or 0
        if operation.scope == ResetScope.FACTORY.value:
            result = session.execute(delete(Setting))
            deleted_counts[Setting.__tablename__] = getattr(result, "rowcount", 0) or 0
            epoch = auth_epoch.get_or_create_row(session)
            epoch.auth_epoch += 1
        operation.phase = "database_cleaned"
        operation.outcome = {
            "status": "running",
            "phase": "database_cleaned",
            "deleted_counts": deleted_counts,
            "target_fingerprint": _target_fingerprint(targets),
        }
        session.commit()
    else:
        # Configuration can change across a crash. Revalidate every recovery and
        # require the exact roots authorized before the database commit.
        targets = validate_reset_targets(config)
        expected_fingerprint = operation.outcome.get("target_fingerprint")
        if expected_fingerprint != _target_fingerprint(targets):
            raise UnsafeResetTarget("managed reset targets changed after authorization")

    if operation.phase == "database_cleaned":
        try:
            _clear_directory_contents(targets.cache)
            _clear_directory_contents(targets.blob)
            if operation.scope == ResetScope.FACTORY.value:
                secret_store.clear()
        except Exception as exc:
            session.expire_all()
            operation = session.get(AdminOperation, operation_id)
            assert operation is not None
            operation.error = "managed storage cleanup incomplete"
            operation.outcome = {
                **operation.outcome,
                "status": "running",
                "phase": "storage_cleanup_incomplete",
            }
            session.commit()
            raise ResetIncomplete(
                "managed storage cleanup incomplete; retry is required"
            ) from exc

        session.expire_all()
        operation = session.get(AdminOperation, operation_id)
        assert operation is not None
        operation.phase = "storage_cleaned"
        operation.error = None
        operation.outcome = {
            **operation.outcome,
            "status": "running",
            "phase": "storage_cleaned",
        }
        session.commit()

    if defer_completion:
        return _result(operation)
    return complete_reset(session, operation_id=operation_id)


def complete_reset(session: Session, *, operation_id: int) -> ResetResult:
    """Release maintenance only after all process-local recovery has succeeded."""

    session.expire_all()
    operation = session.get(AdminOperation, operation_id)
    if operation is None:
        raise ResetError("reset audit record not found")
    if operation.state == "succeeded":
        return _result(operation)
    state = _system_state(session)
    if not state.maintenance_mode or state.active_operation_id != operation.id:
        raise ResetError("reset does not own the maintenance lock")
    if operation.phase != "storage_cleaned":
        raise ResetIncomplete("reset cleanup is not ready for completion")
    operation.state = "succeeded"
    operation.phase = "completed"
    operation.error = None
    operation.completed_at = datetime.now(UTC)
    operation.outcome = {
        **operation.outcome,
        "status": "succeeded",
        "phase": "completed",
        "settings_preserved": operation.scope == ResetScope.CATALOG_AND_ACTIVITY.value,
        "secrets_preserved": operation.scope == ResetScope.CATALOG_AND_ACTIVITY.value,
        "music_files_touched": False,
    }
    state.maintenance_mode = False
    state.active_operation_id = None
    session.commit()
    return _result(operation)


def recover_interrupted_reset(
    session: Session, *, config: Config, secret_store: SecretStore
) -> ResetResult | None:
    state = _system_state(session)
    if not state.maintenance_mode:
        session.commit()
        return None
    if state.active_operation_id is None:
        raise ResetError("maintenance lock has no active reset audit record")
    operation = session.get(AdminOperation, state.active_operation_id)
    if operation is None or operation.state != "running":
        raise ResetError("maintenance lock references an invalid reset audit record")
    return execute_prepared_reset(
        session,
        config=config,
        secret_store=secret_store,
        operation_id=operation.id,
    )


__all__ = [
    "ResetError",
    "ResetIdempotencyConflict",
    "ResetInProgress",
    "ResetIncomplete",
    "ResetResult",
    "ResetScope",
    "UnsafeResetTarget",
    "complete_reset",
    "execute_prepared_reset",
    "maintenance_is_active",
    "prepare_reset",
    "recover_interrupted_reset",
    "request_worker_quiesce",
    "validate_reset_targets",
    "workers_are_quiescent",
]
