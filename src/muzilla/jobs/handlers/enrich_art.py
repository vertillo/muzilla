"""The `enrich_art` job type: fetches album art from Cover Art Archive
for every group with a matched release and no art yet, and stages an
`embed_art` ChangeSet per group (docs/product-spec.md, §9's "embedded
primarily").

One group at a time — network-bound (a CAA fetch), so no CPU-pool
concurrency concern like the ReplayGain handler; CoverArtArchiveProvider
already rate-limits itself via providers/ratelimit.py.
"""

from __future__ import annotations

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import Job, TrackGroup
from muzilla.jobs.cancellation import current_token
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.registry import WorkerContext, register
from muzilla.jobs.worker import JobCancelled
from muzilla.pipeline.cover_assets import register_candidate
from muzilla.pipeline.enrichment import fetch_and_process_art, groups_needing_art
from muzilla.pipeline.proposals import ProposalComposer
from muzilla.pipeline.reviews import OperationDraft, finish_task_attempt, start_task_attempt


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
    art_release_ids = {group.id: group.mb_release_id for group in groups}
    # Matching is proposed, not applied: the DB group has not received its
    # MusicBrainz ID yet. Include active proposal groups using the proposed
    # tag, so optional cover work can start before review/apply.
    known_ids = {group.id for group in groups}
    for group in session.scalars(select(TrackGroup)):
        if group.id in known_ids or not group.tracks:
            continue
        bundle = ProposalComposer.open_bundle_for_group(session, group)
        release_id = (
            ProposalComposer.proposed_tag_value(
                session, bundle.id, track_id=group.tracks[0].id, field="mb_release_id"
            )
            if bundle is not None
            else None
        )
        if isinstance(release_id, str):
            # The fetch below deliberately gets its ID from this non-mutating
            # proposal rather than persisting it prematurely on the group.
            art_release_ids[group.id] = release_id
            groups.append(group)
    requested_bundle_ids: set[int] = set()
    requested_bundle_id = job.payload.get("review_bundle_id")
    if isinstance(requested_bundle_id, int):
        requested_bundle_ids.add(requested_bundle_id)
    raw_bundle_ids = job.payload.get("review_bundle_ids")
    if isinstance(raw_bundle_ids, list):
        requested_bundle_ids.update(
            bundle_id for bundle_id in raw_bundle_ids if isinstance(bundle_id, int)
        )
    if requested_bundle_ids:
        groups = [
            group
            for group in groups
            if (bundle := ProposalComposer.open_bundle_for_group(session, group)) is not None
            and bundle.id in requested_bundle_ids
        ]
    total = len(groups)
    progress.update(0, total=total, message="fetching album art")

    review_ids: list[int] = []
    not_found = 0
    errored = 0
    token = current_token(session, job.id)

    async with httpx.AsyncClient() as client:
        for i, group in enumerate(groups):
            if token.is_requested():
                raise JobCancelled
            release_id = art_release_ids.get(group.id)
            assert isinstance(release_id, str)
            bundle = ProposalComposer.open_bundle_for_group(session, group)
            if bundle is None:
                progress.update(i + 1, total=total)
                continue
            item_key = f"{bundle.scope_type}:{bundle.scope_id}"
            attempt = start_task_attempt(
                session, bundle.id, kind="cover", item_key=item_key, job_id=job.id
            )
            try:
                result = await fetch_and_process_art(
                    client, art_provider, release_id, max_dimension=max_dimension
                )
            except Exception as exc:  # a bad fetch must never abort the whole job
                errored += 1
                progress.log(f"art fetch failed for group {group.id}: {exc}")
                finish_task_attempt(session, attempt, state="transient_failure", error=str(exc))
                session.commit()
                progress.update(i + 1, total=total)
                continue

            if token.is_requested():
                raise JobCancelled

            if result is None:
                not_found += 1
                finish_task_attempt(session, attempt, state="not_found")
                session.commit()
                progress.update(i + 1, total=total)
                continue

            blob = blob_store.put(
                session,
                result.data,
                mime=result.mime,
                width=result.width,
                height=result.height,
            )
            blob.mime = result.mime
            blob.size = len(result.data)
            blob.width = result.width
            blob.height = result.height
            session.flush()
            asset_candidate = register_candidate(
                session,
                bundle.id,
                blob=blob,
                provider="coverartarchive",
            )
            operations = tuple(
                OperationDraft(
                    kind="embed_art",
                    field="art",
                    target_type="track",
                    target_id=track.id,
                    current_value=(
                        {"blob_id": track.art_blob_id} if track.art_blob_id is not None else None
                    ),
                    proposed_value={"blob_id": blob.id},
                    provenance={
                        "section": "cover",
                        "provider": "coverartarchive",
                        "asset_candidate_id": asset_candidate.id,
                        "width": blob.width,
                        "height": blob.height,
                        "mime": blob.mime,
                    },
                )
                for track in group.tracks
                if track.missing_since is None and not track.has_embedded_art
            )
            if operations:
                ProposalComposer(session).add_operations(bundle.id, operations)
                review_ids.append(bundle.id)
                finish_task_attempt(
                    session,
                    attempt,
                    state="succeeded",
                    result={"blob_id": blob.id, "asset_candidate_id": asset_candidate.id},
                )
            else:
                finish_task_attempt(session, attempt, state="not_found")
            session.commit()
            progress.update(i + 1, total=total)

    progress.update(total, total=total, message="art fetch complete")
    return {
        "review_ids": review_ids,
        "embedded": len(review_ids),
        "not_found": not_found,
        "errored": errored,
    }
