"""Loads layered config: packaged defaults -> config files -> env -> CLI overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from muzilla.config.schema import Config

_SEARCH_PATHS = [
    Path("/etc/muzilla/config.yaml"),
]


def _config_dir_path() -> Path | None:
    config_dir = os.environ.get("MUZILLA_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / "config.yaml"
    return None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_file: Path | None = None) -> Config:
    """Load config from files (as init defaults), then let env vars override.

    File values become Pydantic `init` settings, which the schema's
    `settings_customise_sources` ranks below env — so `MUZILLA_*` env vars
    always win over any file, per the documented precedence.
    """
    merged: dict[str, Any] = {}

    paths = list(_SEARCH_PATHS)
    config_dir_path = _config_dir_path()
    if config_dir_path is not None:
        paths.append(config_dir_path)
    if config_file is not None:
        paths.append(config_file)

    for path in paths:
        if path.is_file():
            with path.open() as f:
                data = yaml.safe_load(f) or {}
            merged = _deep_merge(merged, data)

    return Config(**merged)
