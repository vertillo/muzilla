"""Re-exports `muzilla.pipeline.matching` for existing api/cli imports.

The actual implementation lives in `muzilla.pipeline.matching` so
`muzilla.jobs` handlers can call the exact same matching
orchestration a request handler uses — `services` sits above
`jobs`/`pipeline` in the layering contract, so code living only in
`services` would be unreachable from a background job handler.
"""

from __future__ import annotations

from muzilla.pipeline.matching import (
    CandidateRow,
    GroupMatchProposal,
    TrackMatchProposal,
    compose_track_candidate_review,
    propose_group_candidates,
    propose_track_candidates,
    stage_group_match,
    stage_track_match,
)

__all__ = [
    "CandidateRow",
    "GroupMatchProposal",
    "TrackMatchProposal",
    "compose_track_candidate_review",
    "propose_group_candidates",
    "propose_track_candidates",
    "stage_group_match",
    "stage_track_match",
]
