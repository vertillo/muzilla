from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from muzilla.config.schema import Config, StorageConfig
from muzilla.db.models import (
    AdminOperation,
    ApplyRun,
    Blob,
    ImportSession,
    ImportTask,
    Job,
    Operation,
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
from muzilla.jobs import queue, worker
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.set import ProviderSet
from muzilla.services import auth_epoch
from muzilla.services import reset as reset_service
from muzilla.services.reset import (
    ResetIdempotencyConflict,
    ResetIncomplete,
    ResetScope,
    UnsafeResetTarget,
    execute_prepared_reset,
    prepare_reset,
    recover_interrupted_reset,
    request_worker_quiesce,
)
from muzilla.services.reviews import (
    OperationDraft,
    put_revision,
    start_apply_run,
    transition_bundle,
)
from muzilla.services.secrets import FileSecretStore


def _config(tmp_path: Path, migrated_db: Path, *, library_root: Path | None = None) -> Config:
    return Config(
        storage=StorageConfig(
            library_root=library_root or tmp_path / "music",
            data_dir=tmp_path / "data",
            db_path=migrated_db,
            cache_dir=tmp_path / "data" / "cache",
            blob_dir=tmp_path / "data" / "blobs",
            provider_secrets_dir=tmp_path / "data" / "secrets" / "providers",
            backup_dir=tmp_path / "backups",
        )
    )


def _seed_reset_fixture(session: Session, config: Config) -> tuple[Path, str, FileSecretStore]:
    music = config.storage.library_root / "artist - title.flac"
    music.parent.mkdir(parents=True, exist_ok=True)
    music.write_bytes(b"isolated music fixture\x00unchanged")
    config.storage.cache_dir.mkdir(parents=True, exist_ok=True)
    (config.storage.cache_dir / "http.cache").write_bytes(b"cache")
    blob_file = config.storage.blob_dir / "aa" / "blob"
    blob_file.parent.mkdir(parents=True, exist_ok=True)
    blob_file.write_bytes(b"blob")
    assert config.storage.backup_dir is not None
    config.storage.backup_dir.mkdir(parents=True, exist_ok=True)
    (config.storage.backup_dir / "original.flac").write_bytes(b"backup")

    secret_store = FileSecretStore(config.storage.resolved_provider_secrets_dir())
    secret_ref = "providers.discogs.token.fixture"
    secret_store.set(secret_ref, "isolated-secret")

    session.add_all(
        [
            Track(
                path=str(music),
                filename=music.name,
                ext=music.suffix,
                size_bytes=music.stat().st_size,
                mtime_ns=music.stat().st_mtime_ns,
            ),
            Job(type="scan", payload={"root": str(config.storage.library_root)}),
            Blob(
                sha256="a" * 64,
                mime="image/jpeg",
                size=4,
                storage_path="aa/blob",
                refcount=1,
            ),
            Setting(
                key="providers.discogs",
                value={"enabled": True, "secret_ref": secret_ref},
            ),
        ]
    )
    session.commit()
    return music, secret_ref, secret_store


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed_reset_review(session: Session, *, track: Track, index: int, final_state: str) -> None:
    from muzilla.domain.reviews import BundleState

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

    def title_operation(value: str) -> OperationDraft:
        return OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=track.id,
            current_value="Original",
            proposed_value=value,
        )

    import_session = ImportSession(library_root=str(Path(track.path).parent), state="completed")
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
        logical_key=f"reset-test:{index}",
        title=f"Reset review {index}",
        scope_type="track",
        scope_id=track.id,
        source_snapshot=snapshot("Original"),
        operations=(title_operation("First proposal"),),
    )
    bundle = session.get(ReviewBundle, write.bundle_id)
    assert bundle is not None
    bundle.import_session_id = import_session.id
    transition_bundle(session, write.bundle_id, BundleState.READY)
    write = put_revision(
        session,
        bundle_id=write.bundle_id,
        logical_key=f"reset-test:{index}",
        title=f"Reset review {index}",
        scope_type="track",
        scope_id=track.id,
        source_snapshot=snapshot("Externally observed title"),
        operations=(title_operation("Current proposal"),),
    )
    operation = session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"

    if final_state == "ready":
        return

    apply_run = start_apply_run(session, write.bundle_id, idempotency_key=f"reset-apply-{index}")
    apply_run.state = "applied"
    apply_run.result = {"state": "applied", "files": [], "recovery_required": False}
    transition_bundle(session, write.bundle_id, BundleState.APPLIED)
    session.add(
        ReviewFileJournal(
            apply_run_id=apply_run.id,
            track_id=track.id,
            path=track.path,
            phase="tags",
            state="done",
            before_hash="before",
            after_hash="after",
            before_blob={"title": "Original"},
        )
    )
    if final_state == "undone":
        session.add(
            ReviewUndoRun(
                review_bundle_id=write.bundle_id,
                source_apply_run_id=apply_run.id,
                idempotency_key=f"reset-undo-{index}",
                state="undone",
                manifest={"files": []},
                result={"state": "undone", "files": []},
            )
        )


@pytest.mark.parametrize(
    ("scope", "preserves_settings", "preserves_secrets"),
    [
        (ResetScope.CATALOG_AND_ACTIVITY, True, True),
        (ResetScope.FACTORY, False, False),
    ],
)
def test_reset_deletes_ready_applied_and_undone_reviews_with_successive_revisions(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    scope: ResetScope,
    preserves_settings: bool,
    preserves_secrets: bool,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    track = db_session.scalar(select(Track))
    assert track is not None
    for index, final_state in enumerate(("ready", "applied", "undone"), start=1):
        _seed_reset_review(db_session, track=track, index=index, final_state=final_state)
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 3
    assert db_session.scalar(select(func.count()).select_from(ReviewInboxEntry)) == 3
    assert db_session.scalar(select(func.count()).select_from(ImportSession)) == 3
    assert db_session.scalar(select(func.count()).select_from(ImportTask)) == 3
    assert db_session.scalar(select(func.count()).select_from(ProposalRevision)) == 6
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 6
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 2
    assert db_session.scalar(select(func.count()).select_from(ReviewUndoRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 2
    before_hash = _sha256(music)
    before_inode = music.stat().st_ino
    epoch_before = auth_epoch.read_auth_epoch(db_session)

    operation = prepare_reset(
        db_session,
        scope=scope,
        idempotency_key=f"reset-reviews-{scope.value}",
    )
    result = execute_prepared_reset(
        db_session, config=config, secret_store=secret_store, operation_id=operation.id
    )

    assert result.state == "succeeded"
    assert result.settings_preserved is preserves_settings
    assert result.secrets_preserved is preserves_secrets
    assert result.deleted_counts[ReviewBundle.__tablename__] == 3
    assert result.deleted_counts[ProposalRevision.__tablename__] == 6
    assert result.deleted_counts[SourceSnapshot.__tablename__] == 6
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewInboxEntry)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportSession)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportTask)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProposalRevision)) == 0
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewUndoRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0
    assert db_session.execute(text("PRAGMA foreign_key_check")).all() == []
    assert (db_session.get(Setting, "providers.discogs") is not None) is preserves_settings
    assert (secret_store.get(secret_ref) == "isolated-secret") is preserves_secrets
    assert auth_epoch.read_auth_epoch(db_session) == epoch_before + (0 if preserves_settings else 1)
    assert _sha256(music) == before_hash
    assert music.stat().st_ino == before_inode
    assert config.storage.backup_dir is not None
    assert (config.storage.backup_dir / "original.flac").read_bytes() == b"backup"


def test_failed_database_reset_rolls_back_and_retries_during_startup_recovery(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    track = db_session.scalar(select(Track))
    assert track is not None
    for index, final_state in enumerate(("ready", "applied", "undone"), start=1):
        _seed_reset_review(db_session, track=track, index=index, final_state=final_state)
    db_session.commit()
    before_hash = _sha256(music)
    before_inode = music.stat().st_ino
    epoch_before = auth_epoch.read_auth_epoch(db_session)
    operation = prepare_reset(
        db_session,
        scope=ResetScope.FACTORY,
        idempotency_key="faulted-review-reset",
    )
    engine = db_session.get_bind()
    injected = False

    def fail_track_delete(_conn, _cursor, statement, parameters, _context, _executemany):  # type: ignore[no-untyped-def]
        nonlocal injected
        if not injected and statement.lstrip().lower().startswith("delete from tracks"):
            injected = True
            raise OperationalError(statement, parameters, RuntimeError("injected reset DB fault"))

    event.listen(engine, "before_cursor_execute", fail_track_delete)
    try:
        with pytest.raises(ResetIncomplete, match="database cleanup incomplete"):
            execute_prepared_reset(
                db_session,
                config=config,
                secret_store=secret_store,
                operation_id=operation.id,
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail_track_delete)

    assert injected is True
    db_session.expire_all()
    persisted = db_session.get(AdminOperation, operation.id)
    assert persisted is not None and persisted.state == "running" and persisted.phase == "prepared"
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is True
    assert state.active_operation_id == operation.id
    assert db_session.scalar(select(func.count()).select_from(Track)) == 1
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 3
    assert db_session.scalar(select(func.count()).select_from(ReviewInboxEntry)) == 3
    assert db_session.scalar(select(func.count()).select_from(ImportSession)) == 3
    assert db_session.scalar(select(func.count()).select_from(ImportTask)) == 3
    assert db_session.scalar(select(func.count()).select_from(ProposalRevision)) == 6
    assert db_session.get(Setting, "providers.discogs") is not None
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert _sha256(music) == before_hash
    assert music.stat().st_ino == before_inode

    reset_process = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
from muzilla.config.schema import Config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.services.reset import ResetScope, prepare_reset, recover_interrupted_reset
from muzilla.services.secrets import FileSecretStore
config = Config()
engine = create_db_engine(config.storage.db_path)
factory = create_session_factory(engine)
with factory() as session:
    operation = prepare_reset(session, scope=ResetScope.FACTORY, idempotency_key='faulted-review-reset')
    result = recover_interrupted_reset(session, config=config, secret_store=FileSecretStore(config.storage.resolved_provider_secrets_dir()))
    print(json.dumps({'operation_id': operation.id, 'state': result.state if result else None}))
engine.dispose()
""",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "MUZILLA_STORAGE__DB_PATH": str(migrated_db),
            "MUZILLA_STORAGE__LIBRARY_ROOT": str(config.storage.library_root),
            "MUZILLA_STORAGE__DATA_DIR": str(config.storage.data_dir),
            "MUZILLA_STORAGE__CACHE_DIR": str(config.storage.cache_dir),
            "MUZILLA_STORAGE__BLOB_DIR": str(config.storage.blob_dir),
            "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR": str(
                config.storage.resolved_provider_secrets_dir()
            ),
            "MUZILLA_STORAGE__BACKUP_DIR": str(config.storage.backup_dir),
        },
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(reset_process.stdout) == {
        "operation_id": operation.id,
        "state": "succeeded",
    }

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Track)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewBundle)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewInboxEntry)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportSession)) == 0
    assert db_session.scalar(select(func.count()).select_from(ImportTask)) == 0
    assert db_session.scalar(select(func.count()).select_from(ProposalRevision)) == 0
    assert db_session.scalar(select(func.count()).select_from(SourceSnapshot)) == 0
    assert db_session.scalar(select(func.count()).select_from(ApplyRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewUndoRun)) == 0
    assert db_session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0
    assert db_session.get(Setting, "providers.discogs") is None
    assert secret_store.get(secret_ref) is None
    assert auth_epoch.read_auth_epoch(db_session) == epoch_before + 1
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is False
    assert _sha256(music) == before_hash
    assert music.stat().st_ino == before_inode
    assert db_session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_catalog_reset_clears_catalog_activity_cache_and_blobs_but_preserves_music_settings_secret_and_backup(
    db_session: Session, migrated_db: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    before_inode = music.stat().st_ino

    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="catalog-reset-1",
    )
    result = execute_prepared_reset(
        db_session, config=config, secret_store=secret_store, operation_id=operation.id
    )

    assert result.state == "succeeded"
    assert result.settings_preserved is True
    assert result.secrets_preserved is True
    assert result.music_files_touched is False
    assert db_session.scalar(select(func.count()).select_from(Track)) == 0
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0
    assert db_session.scalar(select(func.count()).select_from(Blob)) == 0
    assert db_session.get(Setting, "providers.discogs") is not None
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert list(config.storage.cache_dir.iterdir()) == []
    assert list(config.storage.blob_dir.iterdir()) == []
    assert _sha256(music) == before_hash
    assert music.stat().st_ino == before_inode
    assert (config.storage.backup_dir / "original.flac").read_bytes() == b"backup"  # type: ignore[operator]


def test_factory_reset_removes_settings_and_managed_secrets_revokes_sessions_but_not_music(
    db_session: Session, migrated_db: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    epoch_before = auth_epoch.read_auth_epoch(db_session)

    operation = prepare_reset(
        db_session,
        scope=ResetScope.FACTORY,
        idempotency_key="factory-reset-1",
    )
    result = execute_prepared_reset(
        db_session, config=config, secret_store=secret_store, operation_id=operation.id
    )

    assert result.state == "succeeded"
    assert result.settings_preserved is False
    assert result.secrets_preserved is False
    assert db_session.get(Setting, "providers.discogs") is None
    assert secret_store.get(secret_ref) is None
    assert auth_epoch.read_auth_epoch(db_session) == epoch_before + 1
    assert _sha256(music) == before_hash


def test_reset_rejects_storage_root_overlapping_library_before_any_delete(
    db_session: Session, migrated_db: Path, tmp_path: Path
) -> None:
    library = tmp_path / "music"
    config = _config(tmp_path, migrated_db, library_root=library)
    config.storage.cache_dir = library / "cache"
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)

    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="unsafe-reset",
    )
    with pytest.raises(UnsafeResetTarget, match="library"):
        execute_prepared_reset(
            db_session, config=config, secret_store=secret_store, operation_id=operation.id
        )

    assert db_session.scalar(select(func.count()).select_from(Track)) == 1
    assert db_session.get(Setting, "providers.discogs") is not None
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert _sha256(music) == before_hash
    assert db_session.get(SystemState, 1).maintenance_mode is False  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "broad_root",
    [
        pytest.param(Path("/Users"), id="shallow-users-root"),
        pytest.param(Path(tempfile.gettempdir()), id="global-temp-root"),
    ],
)
def test_reset_rejects_broad_managed_roots_before_any_delete_and_releases_preflight_lock(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    broad_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    safe_cache_file = config.storage.cache_dir / "http.cache"
    config.storage.cache_dir = broad_root
    monkeypatch.setattr(
        reset_service,
        "_clear_directory_contents",
        lambda _root: pytest.fail("broad target reached filesystem deletion"),
    )

    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key=f"broad-root-{broad_root.name or 'anchor'}",
    )
    with pytest.raises(UnsafeResetTarget, match="too broad"):
        execute_prepared_reset(
            db_session,
            config=config,
            secret_store=secret_store,
            operation_id=operation.id,
        )

    assert db_session.scalar(select(func.count()).select_from(Track)) == 1
    assert db_session.get(Setting, "providers.discogs") is not None
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert safe_cache_file.read_bytes() == b"cache"
    assert _sha256(music) == before_hash
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is False


def test_recovery_revalidates_authorized_targets_after_database_cleanup_and_keeps_lock(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, _, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    before_inode = music.stat().st_ino
    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="revalidate-after-db-commit",
    )
    real_clear = reset_service._clear_directory_contents
    monkeypatch.setattr(
        reset_service,
        "_clear_directory_contents",
        lambda _root: (_ for _ in ()).throw(RuntimeError("stop after database commit")),
    )
    with pytest.raises(ResetIncomplete):
        execute_prepared_reset(
            db_session,
            config=config,
            secret_store=secret_store,
            operation_id=operation.id,
        )

    db_session.expire_all()
    persisted = db_session.get(AdminOperation, operation.id)
    assert persisted is not None and persisted.phase == "database_cleaned"
    assert db_session.scalar(select(func.count()).select_from(Track)) == 0

    monkeypatch.setattr(reset_service, "_clear_directory_contents", real_clear)
    config.storage.cache_dir = config.storage.library_root
    with pytest.raises(UnsafeResetTarget, match="library"):
        recover_interrupted_reset(db_session, config=config, secret_store=secret_store)

    db_session.expire_all()
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is True
    assert state.active_operation_id == operation.id
    assert _sha256(music) == before_hash
    assert music.stat().st_ino == before_inode


def test_reset_waits_for_external_leases_to_be_terminal_before_database_or_storage_delete(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    before_inode = music.stat().st_ino
    blob_file = config.storage.blob_dir / "aa" / "blob"
    assert config.storage.backup_dir is not None
    backup_file = config.storage.backup_dir / "original.flac"
    cache_file = config.storage.cache_dir / "http.cache"
    pending = db_session.scalar(select(Job).where(Job.state == "pending"))
    assert pending is not None
    pending.type = "scan"
    pending.payload = {"root": str(config.storage.library_root)}
    db_session.commit()

    apply_started = threading.Event()
    allow_apply_write = threading.Event()

    async def blocked_apply(  # type: ignore[no-untyped-def]
        _session, _job, _progress, _context
    ):
        apply_started.set()
        assert allow_apply_write.wait(timeout=5)
        music.write_bytes(music.read_bytes() + b"\x00external apply completed")
        return {"apply_run_id": 42, "state": "applied", "files": []}

    monkeypatch.setattr(worker, "get_handler", lambda _job_type: blocked_apply)
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    context = WorkerContext(
        provider_set=ProviderSet(metadata={}, art={}, lyrics={}, fingerprint={}, clients=()),
        config=config,
    )
    worker_errors: list[BaseException] = []

    def run_external_worker() -> None:
        try:
            asyncio.run(
                worker.run_one(
                    factory,
                    worker_id="external-cli",
                    config=config.jobs,
                    context=context,
                )
            )
        except BaseException as exc:  # pragma: no cover - re-raised in the test thread
            worker_errors.append(exc)

    external_worker = threading.Thread(target=run_external_worker)
    external_worker.start()
    assert apply_started.wait(timeout=5)
    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="external-worker-quiesce",
    )
    assert request_worker_quiesce(db_session) == 1

    with pytest.raises(reset_service.ResetInProgress, match="worker"):
        execute_prepared_reset(
            db_session,
            config=config,
            secret_store=secret_store,
            operation_id=operation.id,
        )

    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(Track)) == 1
    assert db_session.scalar(select(func.count()).select_from(Blob)) == 1
    assert cache_file.read_bytes() == b"cache"
    assert blob_file.read_bytes() == b"blob"
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert backup_file.read_bytes() == b"backup"
    assert _sha256(music) == before_hash
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is True

    allow_apply_write.set()
    external_worker.join(timeout=5)
    assert external_worker.is_alive() is False
    assert worker_errors == []
    after_apply_hash = _sha256(music)
    assert after_apply_hash != before_hash

    result = execute_prepared_reset(
        db_session,
        config=config,
        secret_store=secret_store,
        operation_id=operation.id,
    )
    assert result.state == "succeeded"
    assert _sha256(music) == after_apply_hash
    assert music.stat().st_ino == before_inode


def test_reset_idempotency_is_persistent_and_scope_bound(
    db_session: Session, migrated_db: Path, tmp_path: Path
) -> None:
    config = _config(tmp_path, migrated_db)
    _, _, secret_store = _seed_reset_fixture(db_session, config)
    first = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="stable-key",
    )
    execute_prepared_reset(
        db_session, config=config, secret_store=secret_store, operation_id=first.id
    )

    replay = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="stable-key",
    )
    assert replay.id == first.id
    assert replay.state == "succeeded"
    assert db_session.scalar(select(func.count()).select_from(AdminOperation)) == 1

    with pytest.raises(ResetIdempotencyConflict):
        prepare_reset(
            db_session,
            scope=ResetScope.FACTORY,
            idempotency_key="stable-key",
        )


def test_persistent_maintenance_lock_blocks_enqueue_and_lease(
    db_session: Session,
) -> None:
    queued = queue.enqueue(db_session, type="scan", payload={})
    prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="maintenance-lock",
    )

    with pytest.raises(queue.MaintenanceModeError):
        queue.enqueue(db_session, type="scan", payload={})
    assert queue.lease_next(db_session, worker_id="worker", lease_seconds=60) is None
    assert db_session.get(Job, queued.id) is not None


def test_interrupted_factory_storage_cleanup_recovers_idempotently_before_restart(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, secret_ref, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    operation = prepare_reset(
        db_session,
        scope=ResetScope.FACTORY,
        idempotency_key="factory-recovery",
    )
    real_clear = secret_store.clear
    monkeypatch.setattr(
        secret_store,
        "clear",
        lambda: (_ for _ in ()).throw(RuntimeError("synthetic isolated cleanup failure")),
    )

    with pytest.raises(ResetIncomplete):
        execute_prepared_reset(
            db_session,
            config=config,
            secret_store=secret_store,
            operation_id=operation.id,
        )
    db_session.expire_all()
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is True
    assert db_session.scalar(select(func.count()).select_from(Track)) == 0
    assert db_session.get(Setting, "providers.discogs") is None
    assert secret_store.get(secret_ref) == "isolated-secret"
    assert _sha256(music) == before_hash

    monkeypatch.setattr(secret_store, "clear", real_clear)
    recovered = recover_interrupted_reset(
        db_session,
        config=config,
        secret_store=secret_store,
    )
    assert recovered is not None and recovered.state == "succeeded"
    db_session.expire_all()
    state = db_session.get(SystemState, 1)
    assert state is not None and state.maintenance_mode is False
    assert secret_store.get(secret_ref) is None
    assert _sha256(music) == before_hash


def test_interrupted_reset_recovers_expired_external_cancelling_lease_before_cleanup(
    db_session: Session,
    migrated_db: Path,
    tmp_path: Path,
) -> None:
    config = _config(tmp_path, migrated_db)
    music, _, secret_store = _seed_reset_fixture(db_session, config)
    before_hash = _sha256(music)
    job = db_session.scalar(select(Job).where(Job.state == "pending"))
    assert job is not None
    job.state = "cancelling"
    job.cancel_requested = True
    job.worker_id = "dead-external-cli"
    job.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    db_session.commit()
    operation = prepare_reset(
        db_session,
        scope=ResetScope.CATALOG_AND_ACTIVITY,
        idempotency_key="expired-external-worker-startup-recovery",
    )

    recovered = recover_interrupted_reset(
        db_session,
        config=config,
        secret_store=secret_store,
    )

    assert recovered is not None
    assert recovered.operation_id == operation.id
    assert recovered.state == "succeeded"
    assert _sha256(music) == before_hash
    assert db_session.scalar(select(func.count()).select_from(Job)) == 0
