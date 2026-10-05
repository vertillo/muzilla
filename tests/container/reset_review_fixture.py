"""Deterministic backend fixtures for the exact-image reset Compose smoke.

This script runs inside the candidate image only as fixture setup and read-only
verification. It never applies a ReviewBundle or changes files under /music.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import cast

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from muzilla.changes.writer import _move_no_clobber, is_case_only_entry_alias
from muzilla.config.schema import Config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import (
    AdminOperation,
    ApplyRun,
    ImportSession,
    ImportTask,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ReviewBundle,
    ReviewFileJournal,
    ReviewInboxEntry,
    ReviewUndoRun,
    Setting,
    SourceSnapshot,
    SystemState,
    Track,
)
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import (
    OperationDraft,
    put_revision,
    start_apply_run,
    transition_bundle,
)
from muzilla.services.auth_epoch import read_auth_epoch
from muzilla.services.secrets import FileSecretStore

_FIXTURE_PATH = Path("/music/reset-review-fixture.mp3")
_SECRET_VALUE = "candidate-reset-managed-secret-not-real"
_REVIEW_STATES = ("ready", "applied", "undone")
_RESET_TABLES = (
    Track,
    ReviewBundle,
    ReviewInboxEntry,
    ImportSession,
    ImportTask,
    Operation,
    OperationAttempt,
    ProposalRevision,
    SourceSnapshot,
    ApplyRun,
    ReviewUndoRun,
    ReviewFileJournal,
)


def _config() -> Config:
    return Config()


def _count(session: Session, model: type[object]) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _seed_review(session: Session, *, track: Track, index: int, final_state: str) -> None:
    def snapshot(title: str) -> dict[str, object]:
        return {
            "items": [
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tags": {"title": title},
                }
            ]
        }

    def operation(value: str) -> OperationDraft:
        return OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=track.id,
            current_value="Original",
            proposed_value=value,
        )

    import_session = ImportSession(library_root=str(_FIXTURE_PATH.parent), state="completed")
    session.add(import_session)
    session.flush()
    session.add(
        ImportTask(
            import_session_id=import_session.id,
            stage="scan",
            seq=0,
            state="done",
        )
    )
    write = put_revision(
        session,
        logical_key=f"candidate-reset:{index}",
        title=f"Reset review {index}",
        scope_type="track",
        scope_id=track.id,
        source_snapshot=snapshot("Original"),
        operations=(operation("First proposal"),),
    )
    bundle = session.get(ReviewBundle, write.bundle_id)
    assert bundle is not None
    bundle.import_session_id = import_session.id
    transition_bundle(session, write.bundle_id, BundleState.READY)
    write = put_revision(
        session,
        bundle_id=write.bundle_id,
        logical_key=f"candidate-reset:{index}",
        title=f"Reset review {index}",
        scope_type="track",
        scope_id=track.id,
        source_snapshot=snapshot("Externally observed title"),
        operations=(operation("Current proposal"),),
    )
    review_operation = session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert review_operation is not None
    review_operation.decision = "accepted"

    if final_state == "ready":
        return

    apply_run = start_apply_run(
        session,
        write.bundle_id,
        idempotency_key=f"candidate-reset-apply-{index}",
    )
    apply_run.state = "applied"
    apply_run.result = {"state": "applied", "files": [], "recovery_required": False}
    apply_run.manifest = {
        **dict(apply_run.manifest),
        "files": [
            {**dict(file_entry), "state": "applied"}
            for file_entry in cast(list[dict[str, object]], apply_run.manifest.get("files", []))
        ],
    }
    for attempt in apply_run.operation_attempts:
        attempt.state = "applied"
    transition_bundle(session, write.bundle_id, BundleState.APPLIED)
    session.add(
        ReviewFileJournal(
            apply_run_id=apply_run.id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="done",
            before_hash=hashlib.sha256(b"fixture-before").hexdigest(),
            after_hash=hashlib.sha256(b"fixture-after").hexdigest(),
            before_blob={"title": "Original"},
        )
    )
    if final_state == "undone":
        session.add(
            ReviewUndoRun(
                review_bundle_id=write.bundle_id,
                source_apply_run_id=apply_run.id,
                idempotency_key=f"candidate-reset-undo-{index}",
                state="undone",
                manifest={"files": []},
                result={"state": "undone", "files": []},
            )
        )


def _assert_seeded(session: Session, *, secret_store: FileSecretStore) -> dict[str, object]:
    bundles = list(session.scalars(select(ReviewBundle).order_by(ReviewBundle.id)))
    assert len(bundles) == 3
    assert sorted(bundle.state for bundle in bundles) == ["applied", "applied", "ready"]
    for bundle in bundles:
        assert [
            revision.revision_no
            for revision in sorted(bundle.revisions, key=lambda r: r.revision_no)
        ] == [
            1,
            2,
        ]
    assert _count(session, ReviewInboxEntry) == 3
    assert _count(session, ProposalRevision) == 6
    assert _count(session, SourceSnapshot) == 6
    assert _count(session, ApplyRun) == 2
    assert _count(session, ReviewUndoRun) == 1
    assert _count(session, ReviewFileJournal) == 2
    assert _count(session, OperationAttempt) == 2
    assert session.scalar(select(ReviewUndoRun.state)) == "undone"
    assert all(run.state == "applied" for run in session.scalars(select(ApplyRun)))
    setting = session.get(Setting, "providers.discogs")
    assert setting is not None
    secret_ref = setting.value.get("secret_ref")
    assert isinstance(secret_ref, str)
    assert setting.value.get("enabled") is False
    assert secret_store.get(secret_ref) == _SECRET_VALUE
    return {
        "ready_reviews": 1,
        "applied_reviews": 1,
        "undone_reviews": 1,
        "revisions": 6,
        "snapshots": 6,
        "apply_runs": 2,
        "undo_runs": 1,
        "journals": 2,
        "auth_epoch": read_auth_epoch(session),
    }


def _assert_clean(
    session: Session, *, scope: str, secret_store: FileSecretStore
) -> dict[str, object]:
    assert all(_count(session, model) == 0 for model in _RESET_TABLES)
    assert session.execute(text("PRAGMA foreign_key_check")).all() == []
    state = session.get(SystemState, 1)
    assert state is not None
    assert state.maintenance_mode is False
    assert state.active_operation_id is None
    secret_root = _config().storage.resolved_provider_secrets_dir()
    secret_files = [path for path in secret_root.rglob("*") if path.is_file()]
    settings_count = _count(session, Setting)
    if scope == "catalog_and_activity":
        setting = session.get(Setting, "providers.discogs")
        assert settings_count == 1 and setting is not None
        secret_ref = setting.value.get("secret_ref")
        assert isinstance(secret_ref, str)
        assert setting.value.get("enabled") is False
        assert secret_store.get(secret_ref) == _SECRET_VALUE
        assert len(secret_files) == 1
    elif scope == "factory":
        assert settings_count == 0
        assert secret_files == []
        assert not any(secret_store.get(path.name) for path in secret_files)
    else:
        raise AssertionError(f"unknown reset scope {scope!r}")
    return {
        "reset_tables_empty": len(_RESET_TABLES),
        "settings": settings_count,
        "managed_secret_files": len(secret_files),
        "auth_epoch": read_auth_epoch(session),
    }


def _audit(session: Session, *, key: str, scope: str, state: str, phase: str) -> dict[str, object]:
    operation = session.scalar(select(AdminOperation).where(AdminOperation.idempotency_key == key))
    assert operation is not None
    assert operation.scope == scope
    assert operation.state == state
    assert operation.phase == phase
    assert operation.actor == "single-user"
    rendered = json.dumps(operation.outcome, sort_keys=True)
    assert _SECRET_VALUE not in rendered
    assert "/music" not in rendered
    assert "candidate-reset-smoke-only-password" not in rendered
    return {
        "operation_id": operation.id,
        "scope": operation.scope,
        "state": operation.state,
        "phase": operation.phase,
        "actor": operation.actor,
    }


def _seed() -> dict[str, object]:
    config = _config()
    assert _FIXTURE_PATH.is_file()
    engine = create_db_engine(config.storage.db_path)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            assert _count(session, Track) == 0
            assert _count(session, ReviewBundle) == 0
            stat_result = _FIXTURE_PATH.stat()
            track = Track(
                path=str(_FIXTURE_PATH),
                filename=_FIXTURE_PATH.name,
                ext=_FIXTURE_PATH.suffix,
                size_bytes=stat_result.st_size,
                mtime_ns=stat_result.st_mtime_ns,
            )
            session.add(track)
            session.flush()
            for index, state in enumerate(_REVIEW_STATES, start=1):
                _seed_review(session, track=track, index=index, final_state=state)
            session.commit()
            return _assert_seeded(
                session,
                secret_store=FileSecretStore(config.storage.resolved_provider_secrets_dir()),
            )
    finally:
        engine.dispose()


def _assert_seeded_command() -> dict[str, object]:
    config = _config()
    engine = create_db_engine(config.storage.db_path)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            return _assert_seeded(
                session,
                secret_store=FileSecretStore(config.storage.resolved_provider_secrets_dir()),
            )
    finally:
        engine.dispose()


def _assert_clean_command(scope: str) -> dict[str, object]:
    config = _config()
    engine = create_db_engine(config.storage.db_path)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            return _assert_clean(
                session,
                scope=scope,
                secret_store=FileSecretStore(config.storage.resolved_provider_secrets_dir()),
            )
    finally:
        engine.dispose()


def _audit_command(key: str, scope: str, state: str, phase: str) -> dict[str, object]:
    config = _config()
    engine = create_db_engine(config.storage.db_path)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            return _audit(session, key=key, scope=scope, state=state, phase=phase)
    finally:
        engine.dispose()


def _assert_interrupted() -> dict[str, object]:
    config = _config()
    engine = create_db_engine(config.storage.db_path)
    try:
        factory = create_session_factory(engine)
        with factory() as session:
            operation = session.scalar(
                select(AdminOperation).where(
                    AdminOperation.idempotency_key == "candidate-factory-reset-fault"
                )
            )
            assert operation is not None
            assert operation.scope == "factory"
            assert operation.state == "running" and operation.phase == "prepared"
            system_state = session.get(SystemState, 1)
            assert system_state is not None
            assert system_state.maintenance_mode is True
            assert system_state.active_operation_id == operation.id
            seeded = _assert_seeded(
                session,
                secret_store=FileSecretStore(config.storage.resolved_provider_secrets_dir()),
            )
            return {"operation_id": operation.id, **seeded}
    finally:
        engine.dispose()


def _case_filesystem_smoke() -> dict[str, object]:
    root = Path("/music/case-only-check")
    root.mkdir(parents=True, exist_ok=True)
    source = root / "Track.mp3"
    destination = root / "track.mp3"
    source.write_bytes(b"exact-image case-only entry")
    assert not destination.exists()
    assert not is_case_only_entry_alias(source, destination)
    original_inode = source.stat().st_ino
    _move_no_clobber(source, destination, same_file=False, library_root=root)
    assert not source.exists()
    assert destination.read_bytes() == b"exact-image case-only entry"
    assert [entry.name for entry in root.iterdir()] == ["track.mp3"]
    assert destination.stat().st_ino == original_inode

    hardlink_source = root / "Linked.mp3"
    hardlink_alias = root / "Linked-alias.mp3"
    hardlink_source.write_bytes(b"hardlink must not bypass no-clobber")
    os.link(hardlink_source, hardlink_alias)
    try:
        _move_no_clobber(
            hardlink_source,
            hardlink_alias,
            same_file=True,
            library_root=root,
        )
    except OSError as exc:
        assert exc.errno == errno.EEXIST
    else:
        raise AssertionError("hard-link alias was accepted as a case-only entry")
    assert hardlink_source.read_bytes() == hardlink_alias.read_bytes()
    assert hardlink_source.stat().st_ino == hardlink_alias.stat().st_ino
    return {
        "filesystem_case_sensitive": True,
        "exact_source_name": source.name,
        "exact_destination_name": destination.name,
        "case_move_preserved_inode": True,
        "hardlink_alias_rejected": True,
    }


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: reset_review_fixture.py seed|assert-seeded|assert-clean|audit|assert-interrupted|case"
        )
    command = sys.argv[1]
    if command == "seed":
        result = _seed()
    elif command == "assert-seeded":
        result = _assert_seeded_command()
    elif command == "assert-clean" and len(sys.argv) == 3:
        result = _assert_clean_command(sys.argv[2])
    elif command == "audit" and len(sys.argv) == 6:
        result = _audit_command(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
    elif command == "assert-interrupted":
        result = _assert_interrupted()
    elif command == "case":
        result = _case_filesystem_smoke()
    else:
        raise SystemExit(f"invalid fixture command: {command}")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
