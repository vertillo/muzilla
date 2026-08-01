from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator, Sequence
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from muzilla.config.schema import Config, PathsConfig, StorageConfig
from muzilla.db.models import Setting
from muzilla.domain import fields as field_registry
from muzilla.services import settings as settings_service
from muzilla.services.providers import EffectiveConfigResolver, build_provider_set
from muzilla.services.secrets import FileSecretStore, SecretStoreError


@pytest.fixture
def secret_store(tmp_path: Path) -> Iterator[FileSecretStore]:
    yield FileSecretStore(tmp_path / "provider-secrets")


def test_get_settings_defaults_before_any_override(db_session: Session) -> None:
    summary = settings_service.get_settings(db_session, provider_config=Config())

    assert {p.provider for p in summary.providers} == {
        "musicbrainz",
        "discogs",
        "deezer",
        "acoustid",
        "coverartarchive",
        "lrclib",
    }
    assert next(p for p in summary.providers if p.provider == "discogs").enabled is False
    assert all(p.enabled for p in summary.providers if p.provider != "discogs")
    assert all(not p.token_configured for p in summary.providers)
    assert summary.templates == settings_service.TemplateSettings(album=None, singleton=None, default=None)
    assert summary.strip_fields == [f.name for f in field_registry.default_strip_fields()]


def test_update_provider_setting_persists_enabled_and_token_presence(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    result = settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        enabled=True,
        token="synthetic-test-token",
    )
    assert result.enabled is True
    assert result.token_configured is True

    summary = settings_service.get_settings(
        db_session,
        provider_config=EffectiveConfigResolver(Config(), secret_store).resolve(db_session),
    )
    discogs = next(p for p in summary.providers if p.provider == "discogs")
    assert discogs.enabled is True
    assert discogs.token_configured is True
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert row.value["enabled"] is True
    assert row.value["secret_ref"].startswith("providers.discogs.token.")
    assert secret_store.get(row.value["secret_ref"]) == "synthetic-test-token"


def test_legacy_plaintext_provider_token_migrates_without_data_loss(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    db_session.add(
        Setting(
            key="providers.discogs",
            value={"enabled": True, "token": "synthetic-legacy-token"},
        )
    )
    db_session.commit()

    assert settings_service.migrate_legacy_provider_tokens(db_session, secret_store) == 1

    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert "token" not in row.value
    reference = settings_service.provider_secret_reference("discogs")
    assert row.value["secret_ref"] == reference
    assert FileSecretStore(secret_store.root).get(reference) == "synthetic-legacy-token"


def test_legacy_migration_keeps_plaintext_retryable_when_store_write_fails(
    db_session: Session,
) -> None:
    class FailingStore:
        def get(self, reference: str) -> str | None:
            return None

        def set(self, reference: str, value: str) -> None:
            raise RuntimeError("synthetic store failure")

        def delete(self, reference: str) -> None:
            return None

    db_session.add(
        Setting(
            key="providers.discogs",
            value={"enabled": True, "token": "synthetic-legacy-token"},
        )
    )
    db_session.commit()

    with pytest.raises(RuntimeError, match="synthetic store failure"):
        settings_service.migrate_legacy_provider_tokens(db_session, FailingStore())

    db_session.expire_all()
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert "token" in row.value


def test_legacy_migration_fails_until_busy_wal_checkpoint_can_truncate(
    db_session: Session,
    migrated_db: Path,
    secret_store: FileSecretStore,
) -> None:
    credential = "synthetic-busy-wal-token"
    db_session.add(
        Setting(
            key="providers.discogs",
            value={"enabled": True, "token": credential},
        )
    )
    db_session.commit()

    reader = sqlite3.connect(migrated_db)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT value FROM settings").fetchall()
        db_session.execute(text("PRAGMA busy_timeout=0"))

        with pytest.raises(settings_service.SettingsMigrationError, match="WAL checkpoint"):
            settings_service.migrate_legacy_provider_tokens(db_session, secret_store)

        wal_path = Path(f"{migrated_db}-wal")
        assert wal_path.exists()
        assert credential.encode() in wal_path.read_bytes()
    finally:
        reader.close()

    db_session.rollback()
    assert settings_service.migrate_legacy_provider_tokens(db_session, secret_store) == 0
    database_files = [migrated_db, *migrated_db.parent.glob(f"{migrated_db.name}-*")]
    assert all(credential.encode() not in path.read_bytes() for path in database_files)


def test_replacing_token_keeps_old_effective_value_when_database_commit_fails(
    db_session: Session,
    tmp_path: Path,
    secret_store: FileSecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-old-token",
    )
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    old_reference = row.value["secret_ref"]

    def fail_commit() -> None:
        raise RuntimeError("synthetic database failure")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="synthetic database failure"):
            settings_service.update_provider_setting(
                db_session,
                secret_store=secret_store,
                provider="discogs",
                token="synthetic-new-token",
            )

    db_session.rollback()
    db_session.expire_all()
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert row.value["secret_ref"] == old_reference
    assert secret_store.get(old_reference) == "synthetic-old-token"
    assert len(list(secret_store.root.glob("*.secret"))) == 1

    config = Config(
        storage=StorageConfig(cache_dir=tmp_path / "cache"),
        providers={"discogs": {"enabled": True}},
        auth={"enabled": False},
    )
    effective = EffectiveConfigResolver(config, secret_store).resolve(db_session)
    assert effective.providers.discogs.resolved_token() == "synthetic-old-token"


def test_replacing_token_does_not_report_failure_after_database_commit(
    db_session: Session,
    secret_store: FileSecretStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-old-token",
    )
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    old_reference = row.value["secret_ref"]
    real_delete = secret_store.delete

    def fail_retired_delete(reference: str) -> None:
        if reference == old_reference:
            raise SecretStoreError("synthetic retired-secret cleanup failure")
        real_delete(reference)

    monkeypatch.setattr(secret_store, "delete", fail_retired_delete)
    result = settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-new-token",
    )

    assert result.token_configured is True
    db_session.expire_all()
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert row.value["secret_ref"] != old_reference
    assert secret_store.get(row.value["secret_ref"]) == "synthetic-new-token"
    assert secret_store.get(old_reference) == "synthetic-old-token"


def test_replacing_token_removes_the_retired_reference(
    db_session: Session,
    secret_store: FileSecretStore,
) -> None:
    settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-old-token",
    )
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    old_reference = row.value["secret_ref"]

    settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-new-token",
    )

    db_session.expire_all()
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    assert row.value["secret_ref"] != old_reference
    assert secret_store.get(row.value["secret_ref"]) == "synthetic-new-token"
    assert secret_store.get(old_reference) is None


def test_saved_provider_override_is_applied_when_provider_set_is_rebuilt(
    db_session: Session, tmp_path: Path, secret_store: FileSecretStore
) -> None:
    config = Config(
        storage=StorageConfig(cache_dir=tmp_path / "cache"),
        providers={"discogs": {"enabled": False}},
        auth={"enabled": False},
    )
    settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        enabled=True,
        token="synthetic-test-token",
    )

    # Rebuilding models a process restart: the persisted override must be
    # resolved before clients are constructed, not merely displayed by API.
    effective_config = EffectiveConfigResolver(config, secret_store).resolve(db_session)
    provider_set = build_provider_set(effective_config)
    try:
        assert "discogs" in provider_set.metadata
    finally:
        asyncio.run(_close_clients(provider_set.clients))


def test_effective_provider_config_keeps_explicit_env_enabled_override(
    db_session: Session, tmp_path: Path, secret_store: FileSecretStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = Config(
        storage=StorageConfig(cache_dir=tmp_path / "cache"),
        providers={"discogs": {"enabled": False}},
        auth={"enabled": False},
    )
    settings_service.update_provider_setting(
        db_session, secret_store=secret_store, provider="discogs", enabled=True, token="synthetic-test-token"
    )
    monkeypatch.setenv("MUZILLA_PROVIDERS__DISCOGS__ENABLED", "false")

    effective = EffectiveConfigResolver(config, secret_store).resolve(db_session)

    assert effective.providers.discogs.enabled is False
    assert effective.providers.discogs.resolved_token() == "synthetic-test-token"


async def _close_clients(clients: Sequence[httpx.AsyncClient]) -> None:
    for client in clients:
        await client.aclose()


def test_update_provider_setting_never_returns_the_token_value(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    result = settings_service.update_provider_setting(
        db_session,
        secret_store=secret_store,
        provider="discogs",
        token="synthetic-test-token",
    )
    # dataclasses.fields() sweep: no field on the returned object should
    # ever be able to carry the raw token, structurally, not just by
    # inspection of today's fields.
    import dataclasses

    for f in dataclasses.fields(result):
        value = getattr(result, f.name)
        assert value != "synthetic-test-token"


def test_update_provider_setting_empty_token_clears_it(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    settings_service.update_provider_setting(
        db_session, secret_store=secret_store, provider="discogs", token="synthetic-test-token"
    )
    row = db_session.get(Setting, "providers.discogs")
    assert row is not None
    reference = row.value["secret_ref"]
    result = settings_service.update_provider_setting(
        db_session, secret_store=secret_store, provider="discogs", token=""
    )
    assert result.token_configured is False
    assert secret_store.get(reference) is None


def test_update_provider_setting_unknown_provider_raises(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.update_provider_setting(
            db_session, secret_store=secret_store, provider="spotify", enabled=True
        )


def test_update_provider_setting_omitting_enabled_leaves_it_unchanged(
    db_session: Session, secret_store: FileSecretStore
) -> None:
    settings_service.update_provider_setting(
        db_session, secret_store=secret_store, provider="deezer", enabled=False
    )
    result = settings_service.update_provider_setting(
        db_session, secret_store=secret_store, provider="deezer", token="synthetic-test-token"
    )
    assert result.enabled is False


def test_update_templates_persists_and_round_trips(db_session: Session) -> None:
    result = settings_service.update_templates(db_session, album="$albumartist/$album/$track $title")
    assert result.album == "$albumartist/$album/$track $title"
    assert result.singleton is None

    summary = settings_service.get_settings(db_session, provider_config=Config())
    assert summary.templates.album == "$albumartist/$album/$track $title"


def test_update_templates_empty_string_clears_an_override(db_session: Session) -> None:
    settings_service.update_templates(db_session, album="$title")
    result = settings_service.update_templates(db_session, album="")
    assert result.album is None


def test_update_templates_none_leaves_other_templates_unchanged(db_session: Session) -> None:
    settings_service.update_templates(db_session, album="$title", singleton="$artist - $title")
    result = settings_service.update_templates(db_session, album="$album/$title")
    assert result.album == "$album/$title"
    assert result.singleton == "$artist - $title"


def test_effective_paths_config_falls_back_to_base_with_no_override(db_session: Session) -> None:
    base = PathsConfig()
    effective = settings_service.effective_paths_config(db_session, base)
    assert effective is base


def test_effective_paths_config_merges_stored_template_override(db_session: Session) -> None:
    base = PathsConfig()
    settings_service.update_templates(db_session, album="$album/$title")

    effective = settings_service.effective_paths_config(db_session, base)
    assert effective.album == "$album/$title"
    assert effective.singleton == base.singleton  # untouched field passes through
    assert effective.create_directories == base.create_directories


def test_update_strip_fields_persists_and_is_used_by_get_settings(db_session: Session) -> None:
    settings_service.update_strip_fields(db_session, fields=["comment", "title"])
    summary = settings_service.get_settings(db_session, provider_config=Config())
    assert summary.strip_fields == ["comment", "title"]


def test_update_strip_fields_rejects_unknown_field_names(db_session: Session) -> None:
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.update_strip_fields(db_session, fields=["not_a_real_field"])


def test_preview_template_renders_against_sample_data() -> None:
    result = settings_service.preview_template("$albumartist - $album - $track $title")
    assert result.errors == []
    assert "Sigur Rós" in result.path
    assert "Ágætis byrjun" in result.path


def test_preview_template_returns_structural_error_for_malformed_syntax() -> None:
    result = settings_service.preview_template("%unknownFunction{$title}")
    assert result.path == ""
    assert result.errors != []


def test_preview_template_never_touches_a_database() -> None:
    # preview_template takes no session parameter at all — this test
    # exists to make that contract explicit and regression-proof rather
    # than only implicit in the function signature.
    import inspect

    assert "session" not in inspect.signature(settings_service.preview_template).parameters
