from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from muzilla.config.schema import Config, StorageConfig
from muzilla.db.engine import create_db_engine
from muzilla.services.migrate import MigrationRunnerNotFoundError, run_migrations

REPO_ROOT = Path(__file__).parent.parent.parent


def _config(db_path: Path) -> Config:
    return Config(storage=StorageConfig(db_path=db_path))


def _run_alembic(db_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"},
        check=check,
        capture_output=True,
        text=True,
    )


def _sqlite_schema(db_path: Path) -> list[tuple[str, str, str]]:
    engine = create_db_engine(db_path)
    try:
        with engine.connect() as connection:
            return list(
                connection.execute(
                    text(
                        "SELECT type, name, sql FROM sqlite_master "
                        "WHERE sql IS NOT NULL ORDER BY type, name"
                    )
                ).tuples()
            )
    finally:
        engine.dispose()


def test_alembic_check_accepts_migrated_schema_and_fts5_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manually managed FTS5 objects must not make a migrated head look stale."""
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "alembic-check.db"
    _run_alembic(db_path, "upgrade", "head")
    schema_before_check = _sqlite_schema(db_path)
    table_names = {name for type_, name, _sql in schema_before_check if type_ == "table"}
    assert {
        "tracks_fts",
        "tracks_fts_data",
        "review_inbox_entries_fts",
        "review_inbox_entries_fts_data",
    } <= table_names

    result = _run_alembic(db_path, "check", check=False)

    assert result.returncode == 0, result.stderr
    assert "Detected removed table" not in result.stderr
    assert _sqlite_schema(db_path) == schema_before_check


def test_alembic_check_still_detects_real_mapped_schema_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "alembic-check-drift.db"
    _run_alembic(db_path, "upgrade", "head")
    engine = create_db_engine(db_path)
    try:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE tracks ADD COLUMN unexpected_alembic_drift TEXT"))
    finally:
        engine.dispose()

    result = _run_alembic(db_path, "check", check=False)

    assert result.returncode != 0
    assert "unexpected_alembic_drift" in result.stderr


@pytest.mark.parametrize(
    "table_name",
    ["tracks_fts_unexpected_alembic_drift", "tracks_fts_content"],
)
def test_alembic_check_detects_unmanaged_table_with_fts_prefix(
    table_name: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A similarly named ordinary table must not be hidden with FTS5 shadows."""
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "alembic-check-fts-prefix-drift.db"
    _run_alembic(db_path, "upgrade", "head")
    engine = create_db_engine(db_path)
    try:
        with engine.begin() as connection:
            connection.execute(text(f"CREATE TABLE {table_name} (id INTEGER)"))
    finally:
        engine.dispose()

    result = _run_alembic(db_path, "check", check=False)

    assert result.returncode != 0
    assert table_name in result.stderr


def test_upgrade_from_0016_to_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An upgrade from revision 0016 creates the current review tables."""
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "upgrade-from-0016.db"
    _run_alembic(db_path, "upgrade", "0016")

    _run_alembic(db_path, "upgrade", "head")

    tables = set(inspect(create_db_engine(db_path)).get_table_names())
    assert {"review_inbox_entries", "review_undo_runs"} <= tables


def test_run_migrations_creates_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "muzilla.db"
    monkeypatch.delenv("MUZILLA_STORAGE__DB_PATH", raising=False)
    monkeypatch.delenv("MUZILLA_ALEMBIC_DB_PATH", raising=False)
    monkeypatch.delenv("MUZILLA_LIBRARY_PATH", raising=False)

    cfg = _config(db_path)
    # Env var host may have overridden storage.db_path; force tmp path for test isolation
    cfg.storage.db_path = db_path
    run_migrations(cfg)

    engine = create_db_engine(db_path)
    tables = set(inspect(engine).get_table_names())
    assert "tracks" in tables
    assert "track_groups" in tables
    assert "review_bundles" in tables
    assert "source_snapshots" in tables
    assert "proposal_revisions" in tables
    assert "operations" in tables
    assert "apply_runs" in tables
    assert "review_undo_runs" in tables
    assert "operation_attempts" in tables
    assert "task_attempts" in tables
    assert "candidate_url_aliases" in tables
    assert "asset_candidates" in tables
    assert "admin_operations" in tables
    assert "system_state" in tables
    assert "field" in {column["name"] for column in inspect(engine).get_columns("operations")}


def test_review_foundation_migration_preserves_legacy_changesets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "legacy.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0010"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-01 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO change_sets
                (id, title, source, source_ref, state, scope_type, scope_id, created_by,
                 stats, created_at, updated_at)
                VALUES (7, 'Legacy draft', 'manual_edit', '{}', 'draft', 'track', 1,
                        'web', '{}', :now, :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0010"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )

    with engine.connect() as connection:
        # COMPAT-CHANGESET-001 / migration 0019 drops legacy change_sets - they are not preserved
        try:
            title = connection.scalar(text("SELECT title FROM change_sets WHERE id = 7"))
            raise AssertionError(f"change_sets table should not exist after head (got {title})")
        except AssertionError:
            raise
        except Exception as exc:
            assert "no such table: change_sets" in str(exc)
        assert connection.scalar(text("SELECT COUNT(*) FROM review_bundles")) == 0


def test_candidate_url_alias_migration_round_trip_enforces_constraints_and_fk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "candidate-url-aliases.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0011"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-02 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO review_bundles
                (id, logical_key, title, scope_type, scope_id, state, error, created_at, updated_at)
                VALUES (1, 'track:1', 'Alias review', 'track', 1, 'ready', NULL, :now, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO source_snapshots
                (id, review_bundle_id, content_digest, payload, created_at)
                VALUES (1, 1, 'snapshot-digest', '{\"items\": []}', :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO proposal_revisions
                (id, review_bundle_id, source_snapshot_id, revision_no, parent_revision_no,
                 content_digest, is_current, candidate_source, candidate_ref, created_at)
                VALUES (1, 1, 1, 1, 0, 'revision-digest', 1, NULL, NULL, :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0012"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert {
        column["name"] for column in inspect(engine).get_columns("candidate_url_aliases")
    } == {
        "id",
        "proposal_revision_id",
        "provider",
        "candidate_type",
        "provider_id",
        "created_at",
    }

    alias_values = {
        "id": 1,
        "proposal_revision_id": 1,
        "provider": "deezer",
        "candidate_type": "track",
        "provider_id": "3135556",
        "created_at": now,
    }
    insert_alias = text(
        """INSERT INTO candidate_url_aliases
        (id, proposal_revision_id, provider, candidate_type, provider_id, created_at)
        VALUES (:id, :proposal_revision_id, :provider, :candidate_type, :provider_id, :created_at)"""
    )
    with engine.begin() as connection:
        connection.execute(insert_alias, alias_values)
        assert connection.scalar(text("SELECT COUNT(*) FROM candidate_url_aliases")) == 1

    duplicate_alias = {**alias_values, "id": 2}
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"), engine.begin() as connection:
        connection.execute(insert_alias, duplicate_alias)
    invalid_alias = {**alias_values, "id": 3, "candidate_type": "playlist"}
    with pytest.raises(IntegrityError, match="CHECK constraint failed"), engine.begin() as connection:
        connection.execute(insert_alias, invalid_alias)

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM review_bundles WHERE id = 1"))
        assert connection.scalar(text("SELECT COUNT(*) FROM candidate_url_aliases")) == 0

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0011"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine.dispose()
    assert "candidate_url_aliases" not in inspect(create_db_engine(db_path)).get_table_names()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert "candidate_url_aliases" in inspect(create_db_engine(db_path)).get_table_names()


def test_asset_candidate_migration_round_trip_enforces_review_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "asset-candidates.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0012"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-08 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO review_bundles
                (id, logical_key, title, scope_type, scope_id, state, error, created_at, updated_at)
                VALUES (1, 'track:1', 'Cover review', 'track', 1, 'ready', NULL, :now, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO blobs
                (id, sha256, mime, size, width, height, storage_path, refcount, created_at)
                VALUES (1, 'sha-1', 'image/jpeg', 10, 300, 300, 'aa/bb/sha-1', 0, :now),
                       (2, 'sha-2', 'image/png', 20, 400, 400, 'aa/bb/sha-2', 0, :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0013"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert "asset_candidates" in inspect(engine).get_table_names()
    insert_candidate = text(
        """INSERT INTO asset_candidates
        (id, review_bundle_id, blob_id, provider, created_at)
        VALUES (:id, :review_bundle_id, :blob_id, :provider, :created_at)"""
    )
    values = {
        "id": 1,
        "review_bundle_id": 1,
        "blob_id": 1,
        "provider": "upload",
        "created_at": now,
    }
    with engine.begin() as connection:
        connection.execute(insert_candidate, values)
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"), engine.begin() as connection:
        connection.execute(insert_candidate, {**values, "id": 2})
    with pytest.raises(IntegrityError, match="CHECK constraint failed"), engine.begin() as connection:
        connection.execute(
            insert_candidate,
            {**values, "id": 3, "blob_id": 2, "provider": ""},
        )

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM blobs WHERE id = 1"))
        assert connection.scalar(text("SELECT COUNT(*) FROM asset_candidates")) == 0
        connection.execute(
            insert_candidate,
            {**values, "id": 4, "blob_id": 2, "provider": "coverartarchive"},
        )
        connection.execute(text("DELETE FROM review_bundles WHERE id = 1"))
        assert connection.scalar(text("SELECT COUNT(*) FROM asset_candidates")) == 0

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0012"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine.dispose()
    assert "asset_candidates" not in inspect(create_db_engine(db_path)).get_table_names()

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert "asset_candidates" in inspect(create_db_engine(db_path)).get_table_names()


def test_review_apply_retry_migration_allows_only_frozen_run_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "review-apply-retry.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0013"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-08 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO review_bundles
                (id, logical_key, title, scope_type, scope_id, state, error, created_at, updated_at)
                VALUES (1, 'track:1', 'Retry review', 'track', 1,
                        'partially_applied', NULL, :now, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO source_snapshots
                (id, review_bundle_id, content_digest, payload, created_at)
                VALUES (1, 1, 'snapshot', '{"items": []}', :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO proposal_revisions
                (id, review_bundle_id, source_snapshot_id, revision_no, parent_revision_no,
                 content_digest, is_current, candidate_source, candidate_ref, created_at)
                VALUES (1, 1, 1, 1, 0, 'revision', 1, NULL, NULL, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO operations
                (id, proposal_revision_id, source_snapshot_id, seq, kind, field,
                 target_type, target_id, current_value, proposed_value, decision,
                 provenance, validation, created_at)
                VALUES (1, 1, 1, 0, 'set_tag', 'title', 'track', 1,
                        '"Before"', '"After"', 'accepted', '{}', '{}', :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0014"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    with engine.begin() as connection:
        connection.execute(text("UPDATE review_bundles SET state = 'applying' WHERE id = 1"))
        connection.execute(
            text("UPDATE review_bundles SET state = 'partially_applied' WHERE id = 1")
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0013"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    with (
        pytest.raises(IntegrityError, match="invalid review bundle state transition"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE review_bundles SET state = 'applying' WHERE id = 1"))

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )


def test_review_bundle_reopen_migration_round_trip_controls_discarded_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "review-bundle-reopen.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0014"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-09 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO review_bundles
                (id, logical_key, title, scope_type, scope_id, state, error, created_at, updated_at)
                VALUES (1, 'track:1', 'Archived review', 'track', 1, 'discarded', NULL, :now, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO source_snapshots
                (id, review_bundle_id, content_digest, payload, created_at)
                VALUES (1, 1, 'snapshot', '{\"items\": []}', :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO proposal_revisions
                (id, review_bundle_id, source_snapshot_id, revision_no, parent_revision_no,
                 content_digest, is_current, candidate_source, candidate_ref, created_at)
                VALUES (1, 1, 1, 1, 0, 'revision', 1, NULL, NULL, :now)"""
            ),
            {"now": now},
        )

    with (
        pytest.raises(IntegrityError, match="invalid review bundle state transition"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE review_bundles SET state = 'ready' WHERE id = 1"))

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0015"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    with engine.begin() as connection:
        connection.execute(text("UPDATE review_bundles SET state = 'ready' WHERE id = 1"))
        connection.execute(text("UPDATE review_bundles SET state = 'discarded' WHERE id = 1"))
        connection.execute(
            text("UPDATE review_bundles SET state = 'needs_attention' WHERE id = 1")
        )
        connection.execute(text("UPDATE review_bundles SET state = 'discarded' WHERE id = 1"))

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0014"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    for state in ("ready", "needs_attention"):
        with (
            pytest.raises(IntegrityError, match="invalid review bundle state transition"),
            engine.begin() as connection,
        ):
            connection.execute(text(f"UPDATE review_bundles SET state = '{state}' WHERE id = 1"))


def test_review_undo_run_migration_round_trip_enforces_single_source_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "review-undo-runs.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0017"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine = create_db_engine(db_path)
    now = "2026-08-11 00:00:00"
    with engine.begin() as connection:
        connection.execute(
            text(
                """INSERT INTO review_bundles
                (id, logical_key, title, scope_type, scope_id, state, error,
                 created_at, updated_at, import_session_id)
                VALUES (1, 'track:1', 'Undo review', 'track', 1, 'applied',
                        NULL, :now, :now, NULL)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO source_snapshots
                (id, review_bundle_id, content_digest, payload, created_at)
                VALUES (1, 1, 'snapshot', '{"items": []}', :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO proposal_revisions
                (id, review_bundle_id, source_snapshot_id, revision_no,
                 parent_revision_no, content_digest, is_current, candidate_source,
                 candidate_ref, candidate_snapshot, match_explanation, confidence,
                 created_at)
                VALUES (1, 1, 1, 1, 0, 'revision', 1, NULL, NULL, NULL, NULL,
                        NULL, :now)"""
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """INSERT INTO apply_runs
                (id, review_bundle_id, proposal_revision_id, idempotency_key, state,
                 manifest, result, error, created_at, updated_at)
                VALUES (1, 1, 1, 'apply', 'applied', '{"files": []}',
                        '{"state": "applied"}', NULL, :now, :now)"""
            ),
            {"now": now},
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0018"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert "review_undo_runs" in inspect(engine).get_table_names()
    insert = text(
        """INSERT INTO review_undo_runs
        (id, review_bundle_id, source_apply_run_id, idempotency_key, state,
         manifest, result, error, created_at, updated_at)
        VALUES (:id, 1, 1, :key, :state, '{"files": []}', NULL, NULL, :now, :now)"""
    )
    with engine.begin() as connection:
        connection.execute(
            insert, {"id": 1, "key": "undo", "state": "pending", "now": now}
        )
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"), engine.begin() as connection:
        connection.execute(
            insert, {"id": 2, "key": "undo-2", "state": "failed", "now": now}
        )
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM review_undo_runs WHERE id = 1"))
    with pytest.raises(IntegrityError, match="CHECK constraint failed"), engine.begin() as connection:
        connection.execute(
            insert, {"id": 3, "key": "undo-3", "state": "unknown", "now": now}
        )

    subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0017"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    engine.dispose()
    assert "review_undo_runs" not in inspect(create_db_engine(db_path)).get_table_names()
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    assert "review_undo_runs" in inspect(create_db_engine(db_path)).get_table_names()


def test_run_migrations_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    config = _config(tmp_path / "muzilla.db")

    run_migrations(config)
    run_migrations(config)  # should not raise or duplicate schema


def test_run_migrations_raises_outside_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(MigrationRunnerNotFoundError):
        run_migrations(_config(tmp_path / "muzilla.db"))


def test_run_migrations_logs_start_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Migration runs are a logged boundary."""
    monkeypatch.chdir(REPO_ROOT)
    with caplog.at_level("INFO", logger="muzilla.services.migrate"):
        run_migrations(_config(tmp_path / "muzilla.db"))

    messages = [r.message for r in caplog.records]
    assert "running migrations" in messages
    assert "migrations complete" in messages


def test_upgrade_0021_adds_duplicate_evidence_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "upgrade-0021.db"
    env = {"MUZILLA_ALEMBIC_DB_PATH": str(db_path), "PATH": "/usr/bin:/bin"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0020"], cwd=REPO_ROOT, env=env, check=True, capture_output=True)
    engine = create_db_engine(db_path)
    cols_before = {c["name"] for c in inspect(engine).get_columns("duplicate_groups")}
    assert "confidence" not in cols_before
    assert "evidence" not in cols_before
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0021"], cwd=REPO_ROOT, env=env, check=True, capture_output=True)
    cols_after = {c["name"] for c in inspect(create_db_engine(db_path)).get_columns("duplicate_groups")}
    assert "confidence" in cols_after
    assert "evidence" in cols_after
    subprocess.run([sys.executable, "-m", "alembic", "downgrade", "0020"], cwd=REPO_ROOT, env=env, check=True, capture_output=True)
    cols_downgraded = {c["name"] for c in inspect(create_db_engine(db_path)).get_columns("duplicate_groups")}
    assert "confidence" not in cols_downgraded
    assert "evidence" not in cols_downgraded
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO_ROOT, env=env, check=True, capture_output=True)
    assert "confidence" in {c["name"] for c in inspect(create_db_engine(db_path)).get_columns("duplicate_groups")}
