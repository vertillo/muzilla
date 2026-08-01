"""The `match` job type: proposes and auto-stages candidates for every
unmatched group, using the exact same orchestration
(`pipeline.matching`) an on-demand API request uses.

Album groups (`kind="album"`) match at the release level via
`propose_group_candidates`/`stage_group_match`; singleton groups
(`kind="singleton"`, exactly one track) match at the recording level
via `propose_track_candidates`/`stage_track_match` (docs/PLAN.md §7
step 5: "Albums match against releases; singletons match against
recordings"). Every changeset this handler stages is tagged with the
owning import_session_id (if any), so the review inbox can find it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.db.models import ChangeSet, Job, TrackGroup
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.matching import (
    propose_group_candidates,
    propose_track_candidates,
    stage_group_match,
    stage_track_match,
)


@register("match")
async def handle_match(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    raw_import_session_id = job.payload.get("import_session_id")
    import_session_id = (
        int(raw_import_session_id) if isinstance(raw_import_session_id, int | str) else None
    )

    groups = list(
        session.scalars(select(TrackGroup).where(TrackGroup.match_state == "unmatched"))
    )
    total = len(groups)
    progress.update(0, total=total, message="matching")

    proposed = 0
    skipped_no_candidates = 0
    token = current_token(session, job.id)

    def cancel_after_fetch() -> None:
        if token.is_requested(force=True):
            # Staging builds a draft in the worker session without committing
            # it.  A cancel that arrives during provider I/O must discard that
            # in-flight proposal before the import orchestrator sees it.
            session.rollback()
            raise JobCancelled(
                {
                    "proposed": proposed,
                    "skipped_no_candidates": skipped_no_candidates,
                    "partial": True,
                }
            )

    for i, group in enumerate(groups):
        if token.is_requested():
            raise JobCancelled

        cs: ChangeSet | None = None
        if group.kind == "singleton":
            tracks = list(group.tracks)
            if not tracks:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            track_proposal = await propose_track_candidates(
                session, context.provider_set, tracks[0].id
            )
            cancel_after_fetch()
            if not track_proposal.candidates:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = track_proposal.candidates[0]
            cs = await stage_track_match(
                session, context.provider_set, tracks[0].id, source=top.source, ref_id=top.ref_id
            )
        else:
            group_proposal = await propose_group_candidates(
                session, context.provider_set, group.id
            )
            cancel_after_fetch()
            if not group_proposal.candidates:
                skipped_no_candidates += 1
                progress.update(i + 1, total=total)
                continue
            top = group_proposal.candidates[0]
            cs = await stage_group_match(
                session, context.provider_set, group.id, source=top.source, ref_id=top.ref_id
            )

        cancel_after_fetch()

        if import_session_id is not None:
            cs.import_session_id = import_session_id
        group.match_state = "proposed"
        session.commit()
        proposed += 1
        progress.update(i + 1, total=total)

    progress.update(total, total=total, message="matching complete")
    return {"proposed": proposed, "skipped_no_candidates": skipped_no_candidates}
