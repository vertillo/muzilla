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


def _is_enrichment_env_overridden(field: str) -> bool:
    key = f"MUZILLA_ENRICHMENT__{field.upper()}"
    return key in os.environ and os.environ[key] != ""


def _is_paths_env_overridden(field: str) -> bool:
    key = f"MUZILLA_PATHS__{field.upper()}"
    return key in os.environ and os.environ[key] != ""


def effective_enrichment_config(session: Session, base: EnrichmentConfig) -> EnrichmentConfig:
    row = _get_row(session, _ENRICHMENT_KEY)
    if row is None:
        return base
    stored = row.value if isinstance(row.value, dict) else {}
    overrides: dict[str, object] = {}
    for enrichment_field in ("metadata_auto", "art_auto", "lyrics_auto", "replaygain_auto"):
        if enrichment_field in stored and not _is_enrichment_env_overridden(enrichment_field):
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
    policy_row = _get_row(session, _PATHS_POLICY_KEY)
    if (
        policy_row is not None
        and "create_directories" in policy_row.value
        and not _is_paths_env_overridden("create_directories")
    ):
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
