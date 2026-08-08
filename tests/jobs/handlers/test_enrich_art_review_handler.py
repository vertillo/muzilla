from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sqlalchemy.orm import Session

from muzilla.audio.art import ProcessedArt
from muzilla.config.schema import Config, EnrichmentConfig, StorageConfig
from muzilla.db.models import Track, TrackGroup
from muzilla.jobs.handlers.enrich_art import handle_enrich_art
from muzilla.jobs.progress import ProgressReporter
from muzilla.jobs.queue import enqueue
from muzilla.jobs.registry import WorkerContext
from muzilla.providers.base import ArtRef, CandidateTrack, ProviderRef, ReleaseCandidate
from muzilla.providers.set import ProviderSet
from muzilla.services.proposals import ProposalComposer


class _StubArtProvider:
    async def get_art(self, ref: object) -> list[ArtRef]:
        return [ArtRef(url="https://example.invalid/cover.jpg", source="coverartarchive")]


def _context(tmp_path: Path) -> WorkerContext:
    return WorkerContext(
        provider_set=ProviderSet(
            metadata={},
            art={"coverartarchive": _StubArtProvider()},  # type: ignore[arg-type]
            lyrics={},
            fingerprint={},
            clients=(),
        ),
        config=Config(
            enrichment=EnrichmentConfig(),
            storage=StorageConfig(blob_dir=tmp_path / "blobs"),
        ),
    )


async def test_provider_cover_becomes_a_selectable_candidate_in_the_same_review(
    db_session: Session, tmp_path: Path
) -> None:
    group = TrackGroup(
        key="provider-cover-group",
        kind="album",
        album="Album",
        mb_release_id="release-1",
    )
    db_session.add(group)
    db_session.flush()
    track = Track(
        path="/music/provider-cover.flac",
        filename="provider-cover.flac",
        ext=".flac",
        size_bytes=1,
        mtime_ns=1,
        title="Old title",
        artist="Artist",
        group_id=group.id,
    )
    db_session.add(track)
    db_session.flush()
    candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Album",
        album_artist="Artist",
        tracks=(CandidateTrack(position=1, title="New title", artist="Artist"),),
    )
    review = ProposalComposer(db_session).compose_candidate_for_scope(
        scope_type="group", scope_id=group.id, candidate=candidate
    )
    job = enqueue(
        db_session,
        type="enrich_art",
        payload={"review_bundle_id": review.id},
    )
    db_session.commit()
    progress = ProgressReporter(db_session, job.id, coalesce_ms=0)

    with patch(
        "muzilla.jobs.handlers.enrich_art.fetch_and_process_art",
        return_value=ProcessedArt(
            data=b"normalized jpeg",
            mime="image/jpeg",
            width=600,
            height=600,
        ),
    ):
        result = await handle_enrich_art(db_session, job, progress, _context(tmp_path))

    assert result["review_ids"] == [review.id]
    detail = ProposalComposer(db_session).choose_cover(review.id, action="keep")
    assert len(detail.cover_candidates) == 1
    cover = detail.cover_candidates[0]
    assert cover.provider == "coverartarchive"
    assert (cover.width, cover.height, cover.mime) == (600, 600, "image/jpeg")
    selected = ProposalComposer(db_session).choose_cover(
        review.id,
        action="select",
        asset_candidate_id=cover.id,
    )
    art = next(
        operation
        for operation in selected.current_revision.operations
        if operation.kind == "embed_art"
    )
    assert art.provenance["asset_candidate_id"] == cover.id
    assert track.has_embedded_art is False
