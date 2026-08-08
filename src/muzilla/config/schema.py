"""Typed configuration schema.

Precedence (low -> high): packaged defaults -> /etc/muzilla/config.yaml ->
$MUZILLA_CONFIG_DIR/config.yaml -> --config file -> settings DB table ->
MUZILLA_* env vars -> CLI flags. The DB `settings` table sits below env
deliberately: in Docker, env is how the operator asserts control, and a
UI toggle must never override an operator's explicit env var.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class ProviderConfig(BaseModel):
    enabled: bool = True
    token: SecretStr | None = None
    token_file: Path | None = None
    base_url_override: str | None = None
    """Points this provider's client at a different base URL — the only
    use case is E2E testing (docs/PLAN.md §11e) against a local mock
    server instead of the real API. `None` uses providers/set.py's
    hardcoded default; never set this in a real deployment."""

    def resolved_token(self) -> str | None:
        if self.token_file is not None:
            return self.token_file.read_text().strip()
        if self.token is not None:
            return self.token.get_secret_value()
        return None


class ProvidersConfig(BaseModel):
    musicbrainz: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=True))
    discogs: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=False))
    deezer: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=True))
    acoustid: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=True))
    """On by default per docs/PLAN.md §3: fingerprinting is a primary
    identification path in a flat, mixed library, not an optional
    enrichment. Still requires a free API key to actually query the
    API — with none configured, build_provider_set simply omits it
    from the built set (graceful degradation, docs/PLAN.md §8), so
    defaulting this to True is safe with no key present."""
    coverartarchive: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=True))
    lrclib: ProviderConfig = Field(default_factory=lambda: ProviderConfig(enabled=True))


class PathsConfig(BaseModel):
    create_directories: bool = False
    # A review should be predictable in a flat, mixed library. Advanced
    # album/folder layouts remain an explicit override, not a hidden default.
    album: str = "$artist - $title"
    singleton: str = "$artist - $title"
    default: str = "$artist - $title"
    overrides: dict[str, str] = Field(default_factory=dict)
    """Query-keyed template overrides, e.g. {"genre:Classical": "..."}
    (docs/PLAN.md §6). Checked in insertion order, first match wins,
    before falling through to album/singleton/default — see
    paths/query.py. Dict insertion order is preserved by Python (since
    3.7) and by PyYAML's safe_load (reads a mapping in file order), so
    this round-trips correctly for the read-only config-file flow this
    project uses. This guarantee would NOT hold if a future feature
    ever re-serializes config back to YAML with yaml.dump's default
    sort_keys=True — no such feature exists today (settings changes go
    through the API/DB, not a YAML rewrite), but flagging it here so a
    future settings-writer doesn't silently reorder override precedence."""
    replace: list[tuple[str, str]] = Field(default_factory=list)
    """Configurable regex substitutions applied during path-component
    sanitization (paths/sanitize.py), each pair (pattern, replacement),
    applied before reserved-character replacement."""


class AuthConfig(BaseModel):
    enabled: bool = True
    password: SecretStr | None = None
    password_file: Path | None = None
    session_secret: SecretStr | None = None
    cookie_secure: bool = False
    """Sets the session cookie's Secure flag. Default False so plain-HTTP
    LAN access keeps working out of the box; set
    MUZILLA_AUTH__COOKIE_SECURE=true when serving through a TLS-
    terminating tunnel (Cloudflare Tunnel, Tailscale), or the session
    cookie can still be sent in the clear over the plain-HTTP LAN hop."""

    def resolved_password(self) -> str | None:
        if self.password_file is not None:
            return self.password_file.read_text().strip()
        if self.password is not None:
            return self.password.get_secret_value()
        return None


class StorageConfig(BaseModel):
    library_root: Path = Path("/music")
    data_dir: Path = Path("/data")
    cache_dir: Path = Path("/data/cache")
    db_path: Path = Path("/data/muzilla.db")
    blob_dir: Path = Path("/data/blobs")
    """Root for changes/blobstore.py's content-addressed art storage —
    deliberately separate from cache_dir (which is safe to wipe; blobs
    back live undo/apply-journal state and must not be)."""
    backup_dir: Path | None = None
    """Root for changes/backup.py's pre-write file backups (docs/PLAN.md
    §11b, Risk #2's "--backup mode copying originals before first
    write"). None disables the feature entirely — distinct from
    ApplyConfig.backup, which is the per-apply-call opt-in; both must be
    set for a backup to actually happen."""
    provider_secrets_dir: Path | None = None
    """Owner-only provider credential store.  It remains file/env bootstrap
    config deliberately: locating secret authority must never depend on the
    database whose export is required to exclude those secrets."""

    def resolved_provider_secrets_dir(self) -> Path:
        return self.provider_secrets_dir or self.db_path.parent / "secrets" / "providers"


class ApplyConfig(BaseModel):
    backup: bool = False
    """Default for the apply job's backup flag when a caller doesn't
    specify one explicitly (docs/PLAN.md §11b) — the CLI/API-level
    per-call flag overrides this, this is just the fallback."""


class RetentionConfig(BaseModel):
    enabled: bool = True
    journal_days: int = 30
    journal_changesets: int = 500
    """Both thresholds from docs/PLAN.md §4/§11c; a journal is pruned
    once EITHER fires, not both."""
    sweep_interval_hours: float = 24.0
    """How often the worker pool's background loop re-runs the sweep,
    in addition to once at startup (docs/PLAN.md §11c)."""


class EnrichmentConfig(BaseModel):
    metadata_auto: bool = True
    art_auto: bool = True
    lyrics_auto: bool = True
    replaygain_auto: bool = True
    network_priority: int = 0
    replaygain_priority: int = -10
    """CPU analysis is deliberately queued below interactive/network work."""
    replaygain_enabled: bool = True
    art_embed_max_dimension: int = Field(default=1200, gt=0)
    """Longest edge fetched art is resized to before embedding (Pillow),
    keeping embedded covers from bloating file sizes — a flat folder
    with 50k+ files can't afford full-resolution CAA scans embedded
    verbatim in every track."""
    art_upload_max_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    """Hard request-body limit for a user-supplied cover."""
    art_upload_max_dimension: int = Field(default=4096, gt=0)
    """Largest accepted source edge before the normal embed resize."""
    art_upload_max_pixels: int = Field(default=16_777_216, gt=0)
    """Decode budget for uploaded covers, independent of compression ratio."""
    art_prefer_existing: bool = True
    """Matches the diff review UI's "keep existing" default (docs/PLAN.md
    §9): local embedded art is often better than CAA's, so enrichment
    proposes replacing it only when the track has none."""
    lyrics_enabled: bool = True


class LoggingConfig(BaseModel):
    level: str = "INFO"
    json_output: bool = True
    """False gives human-readable output for local dev (docs/PLAN.md
    §11d) — production/Docker keeps the default JSON so log
    aggregators can parse it. Named json_output, not json: BaseModel
    already defines a (deprecated pydantic v1-compat) .json() method,
    and a field named `json` shadows it with a UserWarning."""


class MetricsConfig(BaseModel):
    enabled: bool = False
    """docs/PLAN.md §11h: unauthenticated by default (bypasses
    require_auth so a scraper needs no session cookie), so this stays
    opt-in rather than on-by-default — the endpoint exposes library
    size (track/changeset/job counts)."""


class JobsConfig(BaseModel):
    worker_concurrency: int = 2
    """Mini-PC target, risk #6 ("a crashed job can take down the API"):
    bound well below CPU count. 2 lets one import run without an
    ad-hoc match/apply from the UI queuing entirely behind it."""
    poll_interval_seconds: float = 1.0
    job_timeout_seconds: int = 3600
    lease_seconds: int = 120
    """A running job's lease_until = now + lease_seconds, renewed via
    heartbeat; startup recovery reclaims jobs whose lease has expired
    (services.jobs.recover_stuck_jobs)."""
    event_coalesce_ms: int = 250
    """Progress events are coalesced to at most one per this many ms
    per job (docs/PLAN.md §9) — else a 40k-file scan writes 40k rows."""
    cancel_poll_seconds: float = 0.05
    """Maximum interval between persisted cancellation checks at safe
    handler checkpoints.  It bounds DB reads without retaining a stale ORM
    cancellation flag for a long-running job."""


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MUZILLA_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    storage: StorageConfig = Field(default_factory=StorageConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    jobs: JobsConfig = Field(default_factory=JobsConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    apply: ApplyConfig = Field(default_factory=ApplyConfig)
    retention: RetentionConfig = Field(default_factory=RetentionConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    metrics: MetricsConfig = Field(default_factory=MetricsConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # env beats init (file-loaded) settings, matching the documented precedence.
        return (env_settings, dotenv_settings, init_settings, file_secret_settings)
