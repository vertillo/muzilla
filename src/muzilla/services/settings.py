"""DB-backed non-secret settings plus provider secret references.

Also covers filename templates and strip rules. Matching weights remain fixed
module-level constants and are not stored here.

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

import secrets
from contextlib import suppress
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig, PathsConfig
from muzilla.db.models import Setting
from muzilla.domain import fields as field_registry
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import TemplateError
from muzilla.paths.render import Variables, compile_and_render, track_to_variables
from muzilla.pipeline.effective_settings import (
    effective_enrichment_config as _effective_enrichment_config,
)
from muzilla.pipeline.effective_settings import (
    effective_paths_config as _effective_paths_config,
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
# re-export pipeline helpers for backward compat (services layer)
effective_enrichment_config = _effective_enrichment_config
effective_paths_config = _effective_paths_config
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
class SettingsSummary:
    providers: list[ProviderSetting]
    templates: TemplateSettings
    strip_fields: list[str]
    """Effective strip field names: the DB override if one has ever
    been saved, else domain/fields.py's built-in default_strip set."""
    enrichment: EnrichmentSettings
    paths_policy: PathsPolicySettings


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


def provider_setting_from_effective_config(
    provider: str, provider_config: Config
) -> ProviderSetting:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")
    effective = getattr(provider_config.providers, provider)
    return ProviderSetting(
        provider=provider,
        enabled=effective.enabled,
        token_configured=effective.resolved_token() is not None,
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


def get_settings(
    session: Session, *, provider_config: Config, base_config: Config | None = None
) -> SettingsSummary:
    base = base_config or provider_config
    providers = []
    for name in _PROVIDER_NAMES:
        providers.append(provider_setting_from_effective_config(name, provider_config))

    templates_row = _get_row(session, _TEMPLATES_KEY)
    templates_value = templates_row.value if templates_row is not None else {}
    templates = TemplateSettings(
        album=templates_value.get("album"),  # type: ignore[arg-type]
        singleton=templates_value.get("singleton"),  # type: ignore[arg-type]
        default=templates_value.get("default"),  # type: ignore[arg-type]
    )

    enrichment = get_enrichment_settings(session, base.enrichment)
    paths_policy = effective_paths_policy_settings(session, base.paths)

    return SettingsSummary(
        providers=providers,
        templates=templates,
        strip_fields=get_strip_fields(session),
        enrichment=enrichment,
        paths_policy=paths_policy,
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
) -> ProviderSetting:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")

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
