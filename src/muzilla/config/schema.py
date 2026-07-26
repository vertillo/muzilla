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
    album: str = "$albumartist - $album - $track $title"
    singleton: str = "$artist - $title"
    default: str = "$artist - $title"


class AuthConfig(BaseModel):
    enabled: bool = True
    password: SecretStr | None = None
    password_file: Path | None = None
    session_secret: SecretStr | None = None

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
