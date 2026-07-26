"""Re-exports the shared string-distance pipeline.

docs/PLAN.md §3 specs this as `matching/string_dist.py`, but the actual
normalization pipeline lives in `domain/normalize.py` — it's needed by
`pipeline/grouping.py` (Stage 3 tag clustering) too, and `pipeline`
cannot import `matching` (layering contract: `matching` sits above
`pipeline`). Keeping one implementation in `domain` and re-exporting it
here avoids two fuzzy matchers drifting apart.
"""

from __future__ import annotations

from muzilla.domain.normalize import normalize_for_match, string_dist

__all__ = ["normalize_for_match", "string_dist"]
