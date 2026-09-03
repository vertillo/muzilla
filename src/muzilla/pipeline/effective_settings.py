"""Effective settings resolution for enrichment, paths policy, and matching.

This module lives in `pipeline` (below `jobs` and `services`) so both
layers can import it without violating the import-linter `Layers are
one-directional` contract (`jobs -> services` is forbidden). It merges
DB-stored overrides (table `settings`) on top of a `Config` instance,
respecting `MUZILLA_*` env-var precedence (env wins over DB).
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from muzilla.config.schema import (
    Config,
    EnrichmentConfig,
    MatchingConfig,
    PathsConfig,
    RetentionConfig,
)
from muzilla.db.models import Setting

_ENRICHMENT_KEY = "enrichment"
_PATHS_POLICY_KEY = "paths.policy"
_TEMPLATES_KEY = "paths.templates"
_MATCHING_KEY = "matching"
_RETENTION_KEY = "retention"


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


def _is_retention_env_overridden(field: str) -> bool:
    key = f"MUZILLA_RETENTION__{field.upper()}"
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
        effective = effective.model_copy(
            update={"create_directories": _parse_env_bool(os.environ[env_key])}
        )
    elif (
        policy_row := _get_row(session, _PATHS_POLICY_KEY)
    ) is not None and "create_directories" in policy_row.value:
        effective = effective.model_copy(
            update={"create_directories": bool(policy_row.value["create_directories"])}
        )
    return effective


def effective_matching_config(session: Session, base: MatchingConfig) -> MatchingConfig:
    row = _get_row(session, _MATCHING_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    overrides: dict[str, object] = {}
    # Each top-level matching field may be overridden via DB; env wins per-field
    for field in (
        "album_weights",
        "singleton_weights",
        "track_weights",
        "album_strong_threshold",
        "album_reject_threshold",
        "singleton_strong_threshold",
        "singleton_reject_threshold",
        "min_gap",
        "provider_order",
        "source_penalty",
    ):
        env_key = f"MUZILLA_MATCHING__{field.upper()}"
        if env_key in os.environ and os.environ[env_key] != "":
            # JSON-like env for dict/list, plain for scalars
            raw = os.environ[env_key]
            try:
                import json as _json

                overrides[field] = _json.loads(raw)
            except Exception:
                # fallback to raw string for simple scalars
                try:
                    overrides[field] = float(raw)
                except ValueError:
                    overrides[field] = raw
        elif field in stored:
            overrides[field] = stored[field]
    if not overrides:
        return base
    try:
        return base.model_copy(update=overrides)
    except Exception:
        # Corrupt stored values fall back to base; validation will surface on next write
        return base


def _parse_env_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except ValueError:
        return None


def _parse_env_float(value: str) -> float | None:
    try:
        return float(value.strip())
    except ValueError:
        return None


def effective_retention_config(session: Session, base: RetentionConfig) -> RetentionConfig:
    row = _get_row(session, _RETENTION_KEY)
    stored = row.value if row is not None and isinstance(row.value, dict) else {}
    overrides: dict[str, object] = {}
    for field in ("journal_days", "journal_changesets", "sweep_interval_hours", "enabled"):
        env_key = f"MUZILLA_RETENTION__{field.upper()}"
        if env_key in os.environ and os.environ[env_key] != "":
            raw = os.environ[env_key]
            if field == "enabled":
                overrides[field] = _parse_env_bool(raw)
            elif field in ("journal_days", "journal_changesets"):
                parsed_int = _parse_env_int(raw)
                if parsed_int is not None:
                    overrides[field] = parsed_int
            else:
                parsed_float = _parse_env_float(raw)
                if parsed_float is not None:
                    overrides[field] = parsed_float
        elif field in stored:
            overrides[field] = stored[field]
    if not overrides:
        return base
    try:
        return base.model_copy(update=overrides)
    except Exception:
        return base


def get_effective_config(session: Session, base: Config) -> Config:
    return base.model_copy(
        update={
            "enrichment": effective_enrichment_config(session, base.enrichment),
            "paths": effective_paths_config(session, base.paths),
            "matching": effective_matching_config(session, base.matching),
            "retention": effective_retention_config(session, base.retention),
        }
    )
