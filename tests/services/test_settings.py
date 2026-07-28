from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.domain import fields as field_registry
from muzilla.services import settings as settings_service


def test_get_settings_defaults_before_any_override(db_session: Session) -> None:
    summary = settings_service.get_settings(db_session)

    assert {p.provider for p in summary.providers} == {
        "musicbrainz",
        "discogs",
        "deezer",
        "acoustid",
        "coverartarchive",
        "lrclib",
    }
    assert all(p.enabled for p in summary.providers)
    assert all(not p.token_configured for p in summary.providers)
    assert summary.templates == settings_service.TemplateSettings(album=None, singleton=None, default=None)
    assert summary.strip_fields == [f.name for f in field_registry.default_strip_fields()]


def test_update_provider_setting_persists_enabled_and_token_presence(db_session: Session) -> None:
    result = settings_service.update_provider_setting(
        db_session, provider="discogs", enabled=True, token="secret-token-value"
    )
    assert result.enabled is True
    assert result.token_configured is True

    summary = settings_service.get_settings(db_session)
    discogs = next(p for p in summary.providers if p.provider == "discogs")
    assert discogs.enabled is True
    assert discogs.token_configured is True


def test_update_provider_setting_never_returns_the_token_value(db_session: Session) -> None:
    result = settings_service.update_provider_setting(db_session, provider="discogs", token="super-secret")
    # dataclasses.fields() sweep: no field on the returned object should
    # ever be able to carry the raw token, structurally, not just by
    # inspection of today's fields.
    import dataclasses

    for f in dataclasses.fields(result):
        value = getattr(result, f.name)
        assert value != "super-secret"


def test_update_provider_setting_empty_token_clears_it(db_session: Session) -> None:
    settings_service.update_provider_setting(db_session, provider="discogs", token="a-token")
    result = settings_service.update_provider_setting(db_session, provider="discogs", token="")
    assert result.token_configured is False


def test_update_provider_setting_unknown_provider_raises(db_session: Session) -> None:
    with pytest.raises(settings_service.SettingsValidationError):
        settings_service.update_provider_setting(db_session, provider="spotify", enabled=True)


def test_update_provider_setting_omitting_enabled_leaves_it_unchanged(db_session: Session) -> None:
    settings_service.update_provider_setting(db_session, provider="deezer", enabled=False)
    result = settings_service.update_provider_setting(db_session, provider="deezer", token="x")
    assert result.enabled is False


def test_update_templates_persists_and_round_trips(db_session: Session) -> None:
    result = settings_service.update_templates(db_session, album="$albumartist/$album/$track $title")
    assert result.album == "$albumartist/$album/$track $title"
    assert result.singleton is None

    summary = settings_service.get_settings(db_session)
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
    summary = settings_service.get_settings(db_session)
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
