"""Effective settings resolution for enrichment and paths policy.

This module lives in `pipeline` (below `jobs` and `services`) so both
layers can import it without violating the import-linter `Layers are
one-directional` contract (`jobs -> services` is forbidden). It merges
DB-stored overrides (table `settings`) on top of a `Config` instance,
respecting `MUZILLA_*` env-var precedence (env wins over DB).
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from muzilla.config.schema import Config, EnrichmentConfig, PathsConfig
from muzilla.db.models import Setting

_ENRICHMENT_KEY = "enrichment"
_PATHS_POLICY_KEY = "paths.policy"
_TEMPLATES_KEY = "paths.templates"


def _get_row(session: Session, key: str) -> Setting | None:
    return session.get(Setting, key)


def _parse_env_bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes", "on", "t", "y")


def _is_enrichment_env_overridden(field: str) -> bool:
    key = f"MUZILLA_ENRICHMENT__{field.upper()}"
    return key in os.environ and os.environ[key] != ""


def _is_paths_env_overridden(field: str) -> bool:
    key = f"MUZILLA_PATHS__{field.upper()}"
    return key in os.environ and os.environ[key] != ""


def effective_enrichment_config(session: Session, base: EnrichmentConfig) -> EnrichmentConfig:
    row = _get_row(session, _ENRICHMENT_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    overrides: dict[str, object] = {}
    for enrichment_field in ("metadata_auto", "art_auto", "lyrics_auto", "replaygain_auto"):
        env_key = f"MUZILLA_ENRICHMENT__{enrichment_field.upper()}"
        if env_key in os.environ and os.environ[env_key] != "":
            overrides[enrichment_field] = _parse_env_bool(os.environ[env_key])
        elif enrichment_field in stored:
            overrides[enrichment_field] = bool(stored[enrichment_field])
    if not overrides:
        return base
    return base.model_copy(update=overrides)


def effective_paths_config(session: Session, base: PathsConfig) -> PathsConfig:
    """Merges DB template overrides and `create_directories` policy."""
    effective = base
    templates_row = _get_row(session, _TEMPLATES_KEY)
    if templates_row is not None:
        value = templates_row.value
        effective = PathsConfig(
            create_directories=effective.create_directories,
            album=value.get("album", effective.album),  # type: ignore[arg-type]
            singleton=value.get("singleton", effective.singleton),  # type: ignore[arg-type]
            default=value.get("default", effective.default),  # type: ignore[arg-type]
            overrides=effective.overrides,
            replace=effective.replace,
        )
    env_key = "MUZILLA_PATHS__CREATE_DIRECTORIES"
    if env_key in os.environ and os.environ[env_key] != "":
        effective = effective.model_copy(update={"create_directories": _parse_env_bool(os.environ[env_key])})
    elif (policy_row := _get_row(session, _PATHS_POLICY_KEY)) is not None and "create_directories" in policy_row.value:
        effective = effective.model_copy(
            update={"create_directories": bool(policy_row.value["create_directories"])}
        )
    return effective


def get_effective_config(session: Session, base: Config) -> Config:
    return base.model_copy(
        update={
            "enrichment": effective_enrichment_config(session, base.enrichment),
            "paths": effective_paths_config(session, base.paths),
        }
    )
