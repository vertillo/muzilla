"""Re-exports `muzilla.providers.set` for existing api/cli imports.

The actual implementation lives in `muzilla.providers.set` (moved
there in Phase 4) so `muzilla.jobs` handlers can build/use a
`ProviderSet` without violating the layering contract — `services`
sits above `jobs`, and matching orchestration must run identically
from both a request handler and a background job handler.
"""

from __future__ import annotations

from muzilla.providers.set import ProviderSet, build_provider_set, provider_health

__all__ = ["ProviderSet", "build_provider_set", "provider_health"]
