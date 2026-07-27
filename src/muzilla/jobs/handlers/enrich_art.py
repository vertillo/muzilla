"""The `enrich_art` job type: fetches album art from Cover Art Archive
for every group with a matched release and no art yet, and stages an
`embed_art` ChangeSet per group (docs/PLAN.md §Phase-6, §9's "embedded
primarily").

One group at a time — network-bound (a CAA fetch), so no CPU-pool
concurrency concern like the ReplayGain handler; CoverArtArchiveProvider
already rate-limits itself via providers/ratelimit.py.
"""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import Job
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.enrichment import (
    fetch_and_process_art,
    groups_needing_art,
    stage_art_for_group,
)


@register("enrich_art")
async def handle_enrich_art(
    session: Session, job: Job, progress: ProgressReporter, context: WorkerContext
) -> dict[str, object]:
    art_provider = context.provider_set.art.get("coverartarchive")
    if art_provider is None:
        progress.log("coverartarchive not configured — skipping")
        return {"embedded": 0, "not_found": 0, "errored": 0, "skipped": True}

    prefer_existing = context.config.enrichment.art_prefer_existing
    max_dimension = context.config.enrichment.art_embed_max_dimension
    blob_store = BlobStore(context.config.storage.blob_dir)

    groups = groups_needing_art(session, prefer_existing=prefer_existing)
    total = len(groups)
    progress.update(0, total=total, message="fetching album art")

    change_set_ids: list[int] = []
    not_found = 0
    errored = 0

    async with httpx.AsyncClient() as client:
        for i, group in enumerate(groups):
            if job.cancel_requested:
                raise JobCancelled
            assert group.mb_release_id is not None  # guaranteed by groups_needing_art's query
            try:
                result = await fetch_and_process_art(
                    client, art_provider, group.mb_release_id, max_dimension=max_dimension
                )
            except Exception as exc:  # a bad fetch must never abort the whole job
                errored += 1
                progress.log(f"art fetch failed for group {group.id}: {exc}")
                progress.update(i + 1, total=total)
                continue

            if result is None:
                not_found += 1
                progress.update(i + 1, total=total)
                continue

            data, mime = result
            change_set = stage_art_for_group(session, group, blob_store, data, mime)
            if change_set is not None:
                change_set_ids.append(change_set.id)
            session.commit()
            progress.update(i + 1, total=total)

    progress.update(total, total=total, message="art fetch complete")
    return {
        "change_set_ids": change_set_ids,
        "embedded": len(change_set_ids),
        "not_found": not_found,
        "errored": errored,
    }
