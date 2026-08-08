"""Service-facing compatibility exports for path composition."""

from muzilla.pipeline import paths as _pipeline_paths
from muzilla.pipeline.paths import *  # noqa: F403

# Preserve the legacy module-level helper used by path boundary tests and
# compatibility consumers; wildcard imports intentionally omit underscore names.
_build_group_resolver = _pipeline_paths._build_group_resolver
