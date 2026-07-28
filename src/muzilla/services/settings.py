"""DB-backed settings: providers/tokens, filename templates, strip
rules (Phase 7 suggestion #3, docs/PLAN.md §9).

Scope, deliberately narrower than "everything in config/schema.py":

- **Providers/tokens** — override `enabled`/`token` per provider.
- **Filename templates** — override `paths.album`/`paths.singleton`/
  `paths.default`.
- **Strip rules** — override which field names propose_strip() treats
  as default-strip, layered on top of domain/fields.py's built-in set.
- **Weights** — explicitly OUT OF SCOPE for this pass. matching/
  weights.py's ALBUM_WEIGHTS/TRACK_WEIGHTS/SINGLETON_WEIGHTS are
  hardcoded module-level constants imported directly by
  matching/engine.py's scoring functions — making them genuinely
  runtime-configurable means threading a weights parameter through the
  whole matching call chain (engine.py's album/track/singleton scoring,
  every pipeline/matching.py call site), which is a matching-engine
  refactor with real correctness risk, not a settings-storage problem.
  That refactor is a separable follow-up; this pass does not touch
  matching behavior. See docs/PROGRESS.md for the same note recorded
  where a future session will actually look for it.

Provider enabled/token settings persist and round-trip through this
module, but do NOT take effect on already-running provider clients:
services/providers.py's ProviderSet is built once at app startup
(api/app.py's lifespan) from the file/env config, with no rebuild
hook. Making a saved token or enabled flag change a live httpx client
would mean either rebuilding ProviderSet on every settings write or
re-reading settings on every provider call (defeating the point of
building clients once with warm caches) — out of scope here for the
same reason weights are: a real architectural change, not a storage
one. The Settings screen's UI says so explicitly ("takes effect after
a restart"), matching the existing truth for editing config.yaml
directly today — this is not a regression, just an honestly-labeled
limitation. Filename templates and strip rules do NOT have this
problem: services/paths.py and services/strip.py both read their
config fresh per request already (effective_paths_config() below,
get_settings().strip_fields in the strip router), so those two take
effect immediately with no restart.

Bootstrap settings (storage.db_path, auth) are intentionally NOT here:
load_config() runs before any DB connection exists, so nothing DB-
backed can ever affect it — this module only overrides settings read
*after* the app has a session, which is every setting actually listed
above.

Secret handling matches AuthConfig.password's existing SecretStr
convention: a provider token is accepted on write, stored as plain
text in the settings table (same trust boundary as config.yaml, which
also stores tokens in plain text — the DB file has the same access
control as the config file), and never read back out to an API
response. get_settings() returns `token_configured: bool` instead of
the token value; update_provider_setting() accepts a new token
write-only. Grepped for the one place this could leak: no log call
anywhere in this module touches a token value.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.models import Setting
from muzilla.domain import fields as field_registry
from muzilla.paths.context import RenderContext
from muzilla.paths.errors import TemplateError
from muzilla.paths.render import Variables, compile_and_render, track_to_variables

_PROVIDER_NAMES = ("musicbrainz", "discogs", "deezer", "acoustid", "coverartarchive", "lrclib")

_PROVIDERS_KEY_PREFIX = "providers."
_TEMPLATES_KEY = "paths.templates"
_STRIP_FIELDS_KEY = "strip_fields"


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
class SettingsSummary:
    providers: list[ProviderSetting]
    templates: TemplateSettings
    strip_fields: list[str]
    """Effective strip field names: the DB override if one has ever
    been saved, else domain/fields.py's built-in default_strip set."""


def _get_row(session: Session, key: str) -> Setting | None:
    return session.get(Setting, key)


def _upsert(session: Session, key: str, value: dict[str, object]) -> None:
    row = _get_row(session, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    session.commit()


def get_settings(session: Session) -> SettingsSummary:
    providers = []
    for name in _PROVIDER_NAMES:
        row = _get_row(session, f"{_PROVIDERS_KEY_PREFIX}{name}")
        if row is None:
            # No override saved for this provider yet — reads as
            # "enabled, no token" rather than guessing from config.yaml
            # (which this module never reads); the settings screen's
            # baseline is "nothing overridden," not "mirrors the file."
            providers.append(ProviderSetting(provider=name, enabled=True, token_configured=False))
        else:
            providers.append(
                ProviderSetting(
                    provider=name,
                    enabled=bool(row.value.get("enabled", True)),
                    token_configured=bool(row.value.get("token")),
                )
            )

    templates_row = _get_row(session, _TEMPLATES_KEY)
    templates_value = templates_row.value if templates_row is not None else {}
    templates = TemplateSettings(
        album=templates_value.get("album"),  # type: ignore[arg-type]
        singleton=templates_value.get("singleton"),  # type: ignore[arg-type]
        default=templates_value.get("default"),  # type: ignore[arg-type]
    )

    strip_row = _get_row(session, _STRIP_FIELDS_KEY)
    if strip_row is not None:
        stored_fields: object = strip_row.value.get("fields", [])
        strip_fields = list(stored_fields) if isinstance(stored_fields, list) else []
    else:
        strip_fields = [f.name for f in field_registry.default_strip_fields()]

    return SettingsSummary(providers=providers, templates=templates, strip_fields=strip_fields)


class SettingsValidationError(ValueError):
    """Mirrors EditValidationError/PathValidationError elsewhere in
    services/ — api catches ValueError -> 400, same convention."""


def update_provider_setting(
    session: Session, *, provider: str, enabled: bool | None = None, token: str | None = None
) -> ProviderSetting:
    if provider not in _PROVIDER_NAMES:
        raise SettingsValidationError(f"unknown provider: {provider!r}")

    key = f"{_PROVIDERS_KEY_PREFIX}{provider}"
    row = _get_row(session, key)
    current: dict[str, object] = dict(row.value) if row is not None else {"enabled": True}

    if enabled is not None:
        current["enabled"] = enabled
    if token is not None:
        # Empty string clears the token (matches config.schema's
        # resolved_token() treating an absent token as None) rather
        # than storing an empty-but-truthy value.
        if token == "":
            current.pop("token", None)
        else:
            current["token"] = token

    _upsert(session, key, current)
    return ProviderSetting(
        provider=provider,
        enabled=bool(current.get("enabled", True)),
        token_configured=bool(current.get("token")),
    )


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


def effective_paths_config(session: Session, base: PathsConfig) -> PathsConfig:
    """Merges any DB-stored template overrides on top of `base` (the
    file/env-loaded config) — the seam api/routers/paths.py's preview
    and rename endpoints use instead of the bare `config.paths` from
    app.state, so a template edited in Settings actually takes effect
    without a restart. Only album/singleton/default can be overridden
    here; every other PathsConfig field (create_directories, overrides,
    replace) passes through from `base` unchanged, since those aren't
    part of this pass's settings surface."""
    templates_row = _get_row(session, _TEMPLATES_KEY)
    if templates_row is None:
        return base
    value = templates_row.value
    return PathsConfig(
        create_directories=base.create_directories,
        album=value.get("album", base.album),  # type: ignore[arg-type]
        singleton=value.get("singleton", base.singleton),  # type: ignore[arg-type]
        default=value.get("default", base.default),  # type: ignore[arg-type]
        overrides=base.overrides,
        replace=base.replace,
    )


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
aliases (albumartist, track, tracktotal, ...) docs/PLAN.md §6's example
templates use — skipping it would make $albumartist silently render
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
