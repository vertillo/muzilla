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


def test_run_migrations_creates_schema(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    db_path = tmp_path / "muzilla.db"

    run_migrations(_config(db_path))

    engine = create_db_engine(db_path)
    tables = set(inspect(engine).get_table_names())
    assert "tracks" in tables
    assert "track_groups" in tables
    assert "review_bundles" in tables
    assert "source_snapshots" in tables
    assert "proposal_revisions" in tables
    assert "operations" in tables
    assert "apply_runs" in tables
    assert "operation_attempts" in tables
    assert "task_attempts" in tables
    assert "candidate_url_aliases" in tables
    assert "asset_candidates" in tables
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
        assert connection.scalar(text("SELECT title FROM change_sets WHERE id = 7")) == "Legacy draft"
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
    """docs/PLAN.md §11d: migration runs are a logged boundary."""
    monkeypatch.chdir(REPO_ROOT)
    with caplog.at_level("INFO", logger="muzilla.services.migrate"):
        run_migrations(_config(tmp_path / "muzilla.db"))

    messages = [r.message for r in caplog.records]
    assert "running migrations" in messages
    assert "migrations complete" in messages
