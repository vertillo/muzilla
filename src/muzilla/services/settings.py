"""DB-backed non-secret settings plus provider secret references.

Also covers filename templates, strip rules, and advanced matching controls.

The settings covered here are:

- **Providers/tokens** — override `enabled`/`token` per provider.
- **Filename templates** — override `paths.album`/`paths.singleton`/
  `paths.default`.
- **Strip rules** — override which field names propose_strip() treats
  as default-strip, layered on top of domain/fields.py's built-in set.
Provider enabled/token settings persist here; the API resolves their effective
value with the bootstrap configuration and atomically publishes replacement
clients through ``services/providers.py``. Filename templates and strip rules
are read fresh per request already (``effective_paths_config()`` below and
``get_strip_fields()`` in the strip router), so they also take effect
immediately.

Bootstrap settings (storage.db_path, auth) are intentionally NOT here:
load_config() runs before any DB connection exists, so nothing DB-
backed can ever affect it — this module only overrides settings read
*after* the app has a session, which is every setting actually listed
above.

Provider tokens are write-only at the API and live in an owner-only
``SecretStore`` outside the exported SQLite database.  Provider rows contain
only an opaque reference.  ``migrate_legacy_provider_tokens`` durably writes
old plaintext values before removing them from the DB, so interruption is
retryable without losing credentials.
"""

from __future__ import annotations

import os
import secrets
from contextlib import suppress
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from muzilla.config.schema import (
    Config,
    EnrichmentConfig,
    MatchingConfig,
    PathsConfig,
    RetentionConfig,
)
from muzilla.db.models import Setting
from muzilla.domain import fields as field_registry
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import TemplateError
from muzilla.paths.render import Variables, compile_and_render, track_to_variables
from muzilla.pipeline.effective_settings import (
    effective_enrichment_config as _effective_enrichment_config,
)
from muzilla.pipeline.effective_settings import (
    effective_matching_config as _effective_matching_config,
)
from muzilla.pipeline.effective_settings import (
    effective_paths_config as _effective_paths_config,
)
from muzilla.pipeline.effective_settings import (
    effective_retention_config as _effective_retention_config,
)
from muzilla.pipeline.effective_settings import (
    get_effective_config as _get_effective_config,
)
from muzilla.services.secrets import SecretStore, SecretStoreError

_PROVIDER_NAMES = ("musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib")

_PROVIDERS_KEY_PREFIX = "providers."
_TEMPLATES_KEY = "paths.templates"
_STRIP_FIELDS_KEY = "strip_fields"
_ENRICHMENT_KEY = "enrichment"
_PATHS_POLICY_KEY = "paths.policy"
_MATCHING_KEY = "matching"
_RETENTION_KEY = "retention"
# re-export pipeline helpers for backward compat (services layer)
effective_enrichment_config = _effective_enrichment_config
effective_matching_config = _effective_matching_config
effective_paths_config = _effective_paths_config
effective_retention_config = _effective_retention_config
get_effective_config = _get_effective_config


def provider_secret_reference(provider: str) -> str:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")
    return f"providers.{provider}.token"


def _new_provider_secret_reference(provider: str) -> str:
    return f"{provider_secret_reference(provider)}.{secrets.token_hex(16)}"


@dataclass(frozen=True, slots=True)
class ProviderSetting:
    provider: str
    enabled: bool
    token_configured: bool
    """True if a token is stored — the token value itself is never
    returned; write-only from the client's perspective."""
    externally_managed: bool = False
    """True when bootstrap config supplies token/token_file, precedence over UI-managed."""


@dataclass(frozen=True, slots=True)
class TemplateSettings:
    album: str | None
    """None means "use the packaged default from config/schema.py"."""
    singleton: str | None
    default: str | None


@dataclass(frozen=True, slots=True)
class EnrichmentSettings:
    metadata_auto: bool
    art_auto: bool
    lyrics_auto: bool
    replaygain_auto: bool


@dataclass(frozen=True, slots=True)
class PathsPolicySettings:
    create_directories: bool


@dataclass(frozen=True, slots=True)
class MatchingSettings:
    album_weights: dict[str, float]
    singleton_weights: dict[str, float]
    track_weights: dict[str, float]
    album_strong_threshold: float
    album_reject_threshold: float
    singleton_strong_threshold: float
    singleton_reject_threshold: float
    min_gap: float
    provider_order: list[str]
    source_penalty: float


@dataclass(frozen=True, slots=True)
class RetentionSettings:
    enabled: bool
    journal_days: int
    journal_changesets: int
    sweep_interval_hours: float


@dataclass(frozen=True, slots=True)
class SettingsSummary:
    providers: list[ProviderSetting]
    templates: TemplateSettings
    strip_fields: list[str]
    """Effective strip field names: the DB override if one has ever
    been saved, else domain/fields.py's built-in default_strip set."""
    enrichment: EnrichmentSettings
    paths_policy: PathsPolicySettings
    matching: MatchingSettings
    retention: RetentionSettings


def _get_row(session: Session, key: str, *, with_for_update: bool = False) -> Setting | None:
    if with_for_update:
        return session.execute(
            select(Setting).where(Setting.key == key).with_for_update()
        ).scalar_one_or_none()
    return session.get(Setting, key)


def _upsert(session: Session, key: str, value: dict[str, object]) -> None:
    row = _get_row(session, key, with_for_update=True)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def _is_provider_externally_managed(base_config: Config | None, provider: str) -> bool:
    if base_config is None:
        return False
    if provider not in _PROVIDER_NAMES:
        return False
    base = getattr(base_config.providers, provider)
    return base.token is not None or base.token_file is not None


def provider_setting_from_effective_config(
    provider: str, provider_config: Config, base_config: Config | None = None
) -> ProviderSetting:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")
    effective = getattr(provider_config.providers, provider)
    externally_managed = _is_provider_externally_managed(base_config, provider)
    return ProviderSetting(
        provider=provider,
        enabled=effective.enabled,
        token_configured=effective.resolved_token() is not None,
        externally_managed=externally_managed,
    )


def get_strip_fields(session: Session) -> list[str]:
    """Return the only part of Settings consumed outside the Settings API."""
    strip_row = _get_row(session, _STRIP_FIELDS_KEY)
    if strip_row is not None:
        stored_fields: object = strip_row.value.get("fields", [])
        return list(stored_fields) if isinstance(stored_fields, list) else []
    return [f.name for f in field_registry.default_strip_fields()]


def effective_paths_policy_settings(session: Session, base: PathsConfig) -> PathsPolicySettings:
    effective = _effective_paths_config(session, base)
    return PathsPolicySettings(create_directories=effective.create_directories)


def get_enrichment_settings(session: Session, base: EnrichmentConfig) -> EnrichmentSettings:
    effective = _effective_enrichment_config(session, base)
    return EnrichmentSettings(
        metadata_auto=effective.metadata_auto,
        art_auto=effective.art_auto,
        lyrics_auto=effective.lyrics_auto,
        replaygain_auto=effective.replaygain_auto,
    )


def get_matching_settings(session: Session, base: MatchingConfig) -> MatchingSettings:
    effective = _effective_matching_config(session, base)
    return MatchingSettings(
        album_weights=dict(effective.album_weights),
        singleton_weights=dict(effective.singleton_weights),
        track_weights=dict(effective.track_weights),
        album_strong_threshold=effective.album_strong_threshold,
        album_reject_threshold=effective.album_reject_threshold,
        singleton_strong_threshold=effective.singleton_strong_threshold,
        singleton_reject_threshold=effective.singleton_reject_threshold,
        min_gap=effective.min_gap,
        provider_order=list(effective.provider_order),
        source_penalty=effective.source_penalty,
    )


def get_retention_settings(session: Session, base: RetentionConfig) -> RetentionSettings:
    effective = _effective_retention_config(session, base)
    return RetentionSettings(
        enabled=effective.enabled,
        journal_days=effective.journal_days,
        journal_changesets=effective.journal_changesets,
        sweep_interval_hours=effective.sweep_interval_hours,
    )


def is_retention_env_overridden(field: str) -> bool:
    return (
        f"MUZILLA_RETENTION__{field.upper()}" in os.environ
        and os.environ[f"MUZILLA_RETENTION__{field.upper()}"] != ""
    )


def get_settings(
    session: Session, *, provider_config: Config, base_config: Config | None = None
) -> SettingsSummary:
    base = base_config or provider_config
    providers = []
    for name in _PROVIDER_NAMES:
        providers.append(provider_setting_from_effective_config(name, provider_config, base_config))

    templates_row = _get_row(session, _TEMPLATES_KEY)
    templates_value = templates_row.value if templates_row is not None else {}
    templates = TemplateSettings(
        album=templates_value.get("album"),  # type: ignore[arg-type]
        singleton=templates_value.get("singleton"),  # type: ignore[arg-type]
        default=templates_value.get("default"),  # type: ignore[arg-type]
    )

    enrichment = get_enrichment_settings(session, base.enrichment)
    paths_policy = effective_paths_policy_settings(session, base.paths)
    matching = get_matching_settings(session, base.matching)
    retention = get_retention_settings(session, base.retention)

    return SettingsSummary(
        providers=providers,
        templates=templates,
        strip_fields=get_strip_fields(session),
        enrichment=enrichment,
        paths_policy=paths_policy,
        matching=matching,
        retention=retention,
    )


class SettingsValidationError(ValueError):
    """Mirrors EditValidationError/PathValidationError elsewhere in
    services/ — api catches ValueError -> 400, same convention."""


class SettingsMigrationError(RuntimeError):
    """A startup data migration did not reach its required durable state."""


def update_provider_setting(
    session: Session,
    *,
    secret_store: SecretStore,
    provider: str,
    enabled: bool | None = None,
    token: str | None = None,
    base_config: Config | None = None,
) -> ProviderSetting:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")
    if token is not None and _is_provider_externally_managed(base_config, provider):
        raise SettingsValidationError(
            f"provider {provider!r} credential is externally managed and cannot be overwritten via API"
        )

    key = f"{_PROVIDERS_KEY_PREFIX}{provider}"
    row = _get_row(session, key)
    current: dict[str, object] = dict(row.value) if row is not None else {"enabled": True}
    previous_reference_value = current.get("secret_ref")
    previous_reference = (
        previous_reference_value if isinstance(previous_reference_value, str) else None
    )
    created_reference: str | None = None

    # Direct service callers can encounter a legacy row before startup's
    # bulk migration. Preserve write-first ordering, but do not overwrite an
    # already-published reference before the DB commit succeeds.
    legacy_token = current.pop("token", None)
    if token is None and isinstance(legacy_token, str) and legacy_token:
        created_reference = _new_provider_secret_reference(provider)
        secret_store.set(created_reference, legacy_token)
        current["secret_ref"] = created_reference

    if enabled is not None:
        current["enabled"] = enabled
    if token is not None:
        # Empty string clears the token (matches config.schema's
        # resolved_token() treating an absent token as None) rather
        # than storing an empty-but-truthy value.
        if token == "":
            current.pop("secret_ref", None)
        else:
            created_reference = _new_provider_secret_reference(provider)
            secret_store.set(created_reference, token)
            current["secret_ref"] = created_reference

    try:
        _upsert(session, key, current)
    except Exception:
        if created_reference is not None:
            try:
                secret_store.delete(created_reference)
            except Exception as cleanup_error:
                raise SecretStoreError(
                    "cannot clean up a failed provider credential update"
                ) from cleanup_error
        raise

    if previous_reference is not None and created_reference is not None:
        # DB first, then deletion: a crash can leave an unreferenced secret,
        # but can never leave a live DB reference whose credential was lost.
        # Failure to remove the retired value must not turn a committed save
        # into an error whose new credential becomes effective only on restart.
        with suppress(SecretStoreError):
            secret_store.delete(previous_reference)
    elif previous_reference is not None and token == "":
        # Clear remains retryable: its only purpose is removing the referenced
        # value, so a deletion failure must be visible to the caller.
        secret_store.delete(previous_reference)
    return ProviderSetting(
        provider=provider,
        enabled=bool(current.get("enabled", True)),
        token_configured=bool(current.get("secret_ref")),
        externally_managed=_is_provider_externally_managed(base_config, provider),
    )


def migrate_legacy_provider_tokens(session: Session, secret_store: SecretStore) -> int:
    """Moves legacy plaintext provider tokens out of SQLite.

    Each secret write is durable before the single DB commit removes plaintext.
    If any write or the commit fails, startup fails and the legacy rows remain
    retryable.  Successfully written orphan files use stable references and are
    safely overwritten on the next attempt.
    """

    # This must be enabled before updating a legacy row; otherwise SQLite can
    # leave the removed JSON bytes in a free cell even though SELECT no longer
    # exposes them.
    session.execute(text("PRAGMA secure_delete = ON"))
    migrated = 0
    has_secret_reference = False
    for provider in _PROVIDER_NAMES:
        row = _get_row(session, f"{_PROVIDERS_KEY_PREFIX}{provider}")
        if row is None:
            continue
        has_secret_reference = has_secret_reference or "secret_ref" in row.value
        if "token" not in row.value:
            continue
        current = dict(row.value)
        legacy_token = current.pop("token", None)
        if isinstance(legacy_token, str) and legacy_token:
            reference = provider_secret_reference(provider)
            secret_store.set(reference, legacy_token)
            current["secret_ref"] = reference
        row.value = current
        migrated += 1
    if migrated:
        session.commit()
        has_secret_reference = True
    if has_secret_reference:
        # Truncate old WAL frames after the secure delete reaches the
        # main database.  Running this again on restart is harmless and lets a
        # failed checkpoint be retried even though the JSON row is migrated.
        checkpoint = session.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).one()
        busy, log_frames, _checkpointed_frames = (int(value) for value in checkpoint)
        if busy != 0 or log_frames != 0:
            session.rollback()
            raise SettingsMigrationError(
                "provider secret migration WAL checkpoint did not complete"
            )
        session.commit()
    return migrated


def update_templates(
    session: Session,
    *,
    album: str | None = None,
    singleton: str | None = None,
    default: str | None = None,
) -> TemplateSettings:
    """Each argument left None means "don't change this template";
    pass an empty string to explicitly clear an override back to the
    packaged default (mirrors update_provider_setting's token-clearing
    convention)."""
    row = _get_row(session, _TEMPLATES_KEY)
    current: dict[str, object] = dict(row.value) if row is not None else {}

    for key, value in (("album", album), ("singleton", singleton), ("default", default)):
        if value is None:
            continue
        if value == "":
            current.pop(key, None)
        else:
            current[key] = value

    _upsert(session, _TEMPLATES_KEY, current)
    return TemplateSettings(
        album=current.get("album"),  # type: ignore[arg-type]
        singleton=current.get("singleton"),  # type: ignore[arg-type]
        default=current.get("default"),  # type: ignore[arg-type]
    )


def update_enrichment_settings(
    session: Session,
    base: EnrichmentConfig,
    *,
    metadata_auto: bool | None = None,
    art_auto: bool | None = None,
    lyrics_auto: bool | None = None,
    replaygain_auto: bool | None = None,
) -> EnrichmentSettings:
    row = _get_row(session, _ENRICHMENT_KEY, with_for_update=True)
    current: dict[str, object] = (
        dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    )
    for enrichment_field, value in (
        ("metadata_auto", metadata_auto),
        ("art_auto", art_auto),
        ("lyrics_auto", lyrics_auto),
        ("replaygain_auto", replaygain_auto),
    ):
        if value is None:
            continue
        if not isinstance(value, bool):
            raise SettingsValidationError(f"{enrichment_field} must be a boolean")
        current[enrichment_field] = value
    _upsert(session, _ENRICHMENT_KEY, current)
    effective = _effective_enrichment_config(session, base)
    return EnrichmentSettings(
        metadata_auto=effective.metadata_auto,
        art_auto=effective.art_auto,
        lyrics_auto=effective.lyrics_auto,
        replaygain_auto=effective.replaygain_auto,
    )


def update_paths_policy(
    session: Session,
    base: PathsConfig,
    *,
    create_directories: bool | None = None,
) -> PathsPolicySettings:
    row = _get_row(session, _PATHS_POLICY_KEY, with_for_update=True)
    current: dict[str, object] = (
        dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    )
    if create_directories is not None:
        if not isinstance(create_directories, bool):
            raise SettingsValidationError("create_directories must be a boolean")
        current["create_directories"] = create_directories
    _upsert(session, _PATHS_POLICY_KEY, current)
    effective = _effective_paths_config(session, base)
    return PathsPolicySettings(create_directories=effective.create_directories)


def update_matching_settings(
    session: Session,
    base: MatchingConfig,
    *,
    album_weights: dict[str, float] | None = None,
    singleton_weights: dict[str, float] | None = None,
    track_weights: dict[str, float] | None = None,
    album_strong_threshold: float | None = None,
    album_reject_threshold: float | None = None,
    singleton_strong_threshold: float | None = None,
    singleton_reject_threshold: float | None = None,
    min_gap: float | None = None,
    provider_order: list[str] | None = None,
    source_penalty: float | None = None,
) -> MatchingSettings:
    row = _get_row(session, _MATCHING_KEY, with_for_update=True)
    current: dict[str, object] = (
        dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    )
    updates: dict[str, object] = {}
    if album_weights is not None:
        updates["album_weights"] = album_weights
    if singleton_weights is not None:
        updates["singleton_weights"] = singleton_weights
    if track_weights is not None:
        updates["track_weights"] = track_weights
    if album_strong_threshold is not None:
        updates["album_strong_threshold"] = album_strong_threshold
    if album_reject_threshold is not None:
        updates["album_reject_threshold"] = album_reject_threshold
    if singleton_strong_threshold is not None:
        updates["singleton_strong_threshold"] = singleton_strong_threshold
    if singleton_reject_threshold is not None:
        updates["singleton_reject_threshold"] = singleton_reject_threshold
    if min_gap is not None:
        updates["min_gap"] = min_gap
    if provider_order is not None:
        updates["provider_order"] = provider_order
    if source_penalty is not None:
        updates["source_penalty"] = source_penalty
    # Validate by constructing a MatchingConfig from base + current + updates
    merged_raw: dict[str, object] = {}
    # Start from effective base values then overlay stored then updates, to trigger full validation
    base_dict = base.model_dump()
    for k in base_dict:
        merged_raw[k] = current.get(k, base_dict[k])
    for k, v in updates.items():
        merged_raw[k] = v
    try:
        MatchingConfig(**merged_raw)  # type: ignore[arg-type]
    except Exception as exc:
        raise SettingsValidationError(str(exc)) from exc
    for k, v in updates.items():
        current[k] = v
    _upsert(session, _MATCHING_KEY, current)
    return get_matching_settings(session, base)


def reset_matching_settings(session: Session, base: MatchingConfig) -> MatchingSettings:
    row = _get_row(session, _MATCHING_KEY, with_for_update=True)
    if row is not None:
        session.delete(row)
        session.commit()
    return get_matching_settings(session, base)


def update_retention_settings(
    session: Session,
    base: RetentionConfig,
    *,
    enabled: bool | None = None,
    journal_days: int | None = None,
    journal_changesets: int | None = None,
    sweep_interval_hours: float | None = None,
) -> RetentionSettings:
    row = _get_row(session, _RETENTION_KEY, with_for_update=True)
    current: dict[str, object] = (
        dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    )
    updates: dict[str, object] = {}
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise SettingsValidationError("enabled must be a boolean")
        updates["enabled"] = enabled
    if journal_days is not None:
        updates["journal_days"] = journal_days
    if journal_changesets is not None:
        updates["journal_changesets"] = journal_changesets
    if sweep_interval_hours is not None:
        updates["sweep_interval_hours"] = sweep_interval_hours
    merged_raw: dict[str, object] = {}
    base_dict = base.model_dump()
    for k in base_dict:
        merged_raw[k] = current.get(k, base_dict[k])
    for k, v in updates.items():
        merged_raw[k] = v
    try:
        RetentionConfig(**merged_raw)  # type: ignore[arg-type]
    except Exception as exc:
        raise SettingsValidationError(str(exc)) from exc
    for k, v in updates.items():
        current[k] = v
    _upsert(session, _RETENTION_KEY, current)
    return get_retention_settings(session, base)


def reset_retention_settings(session: Session, base: RetentionConfig) -> RetentionSettings:
    row = _get_row(session, _RETENTION_KEY, with_for_update=True)
    if row is not None:
        session.delete(row)
        session.commit()
    return get_retention_settings(session, base)


def update_strip_fields(session: Session, *, fields: list[str]) -> list[str]:
    """Validates every name against domain/fields.py's registry before
    storing — an unknown field name here would silently no-op in
    propose_strip() (getattr(track, field_name, None) always None),
    which is a worse failure mode than rejecting it up front."""
    unknown = [f for f in fields if f not in field_registry.FIELDS]
    if unknown:
        raise SettingsValidationError(f"unknown field name(s): {', '.join(unknown)}")
    _upsert(session, _STRIP_FIELDS_KEY, {"fields": fields})
    return fields


@dataclass(frozen=True, slots=True)
class TemplatePreviewResult:
    path: str
    errors: list[str] = field(default_factory=list)


_SAMPLE_VARIABLES: Variables = track_to_variables(
    {
        "title": "Svefn-g-englar",
        "artist": "Sigur Rós",
        "album_artist": "Sigur Rós",
        "album": "Ágætis byrjun",
        "track_no": 1,
        "track_total": 8,
        "disc_no": 1,
        "disc_total": 1,
        "year": 1999,
        "genre": ("Post-Rock",),
        "label": "Fat Cat Records",
        "catalog_number": "FATCD11",
    }
)
"""A representative track's field values (Sigur Rós — Ágætis byrjun,
the same fixture used throughout this codebase's frontend, e.g.
lib/diff.ts's characterization tests) — a real-looking preview is more
useful for judging a template than placeholder tokens like $title
would be, and it doubles as continuity with every other example in
this app that already uses this album.

Routed through track_to_variables() rather than used as a raw Variables
dict directly: that function is what populates the beets-style short
aliases (albumartist, track, tracktotal, ...) used by the example templates —
skipping it would make $albumartist silently render
empty in the preview despite working identically at actual rename time,
which is confusing (and untrue) enough to be worth avoiding here."""


def preview_template(template_source: str) -> TemplatePreviewResult:
    """Dry-run render of a candidate template string against sample
    data — never touches a real Track or the filesystem. Used by the
    Settings screen's live template editor; paths/render.py's
    compile_and_render already returns a structural result rather than
    raising for render-time issues, so only a compile-time TemplateError
    (malformed syntax) needs catching here."""
    ctx = RenderContext(values=_SAMPLE_VARIABLES)
    try:
        result = compile_and_render(template_source, ctx, create_directories=True)
    except TemplateError as exc:
        return TemplatePreviewResult(path="", errors=[str(exc)])
    return TemplatePreviewResult(path=result.path, errors=list(result.errors))
