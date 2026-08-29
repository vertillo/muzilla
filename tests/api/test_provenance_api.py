"""API regression for MATCHING-PROVENANCE-001: manual edit -> 409 -> force.

Covers bands: manual edit creates pending review, provider import without force
must return 409 confirmation_required when overlapping manual fields, with force
it succeeds and provider field overwrites. Also preserves provenance.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.api.deps import get_provider_set
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track
from muzilla.providers.base import (
    CandidateTrack,
    Capability,
    ProviderRef,
    ReleaseCandidate,
    ReleaseQuery,
)
from muzilla.services.proposals import ProposalComposer
from muzilla.services.providers import ProviderSet


@dataclass
class StubProvider:
    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    capabilities = frozenset({Capability.SEARCH_RELEASES, Capability.GET_RELEASE})

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        return list(self.releases.values())[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        return self.releases.get(ref.id)

    async def health(self) -> object:  # pragma: no cover
        from muzilla.providers.base import ProviderHealth

        return ProviderHealth(name="stub", healthy=True)


def test_manual_edit_blocks_provider_import_until_forced(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # setup track and manual edit via ProposalComposer
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        track = Track(
            path="/music/manual_provenance.mp3",
            filename="manual_provenance.mp3",
            ext=".mp3",
            size_bytes=1,
            mtime_ns=1,
            title="Original Title",
            artist="Original Artist",
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.flush()
        track_id = track.id
        composer = ProposalComposer(session)
        # manual edit of title
        composer.compose_manual_track_edit(
            track_id=track_id, field_values={"title": "Manual Title"}
        )
        session.commit()

    # stub provider that would overwrite title
    provider_candidate = ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Album",
        album_artist="Artist",
        year=1999,
        tracks=(
            CandidateTrack(position=1, title="Provider Title", artist="Artist", duration_ms=200000),
        ),
    )
    stub = StubProvider(releases={"release-1": provider_candidate})
    provider_set = ProviderSet(
        metadata={"musicbrainz": stub}, art={}, lyrics={}, fingerprint={}, clients=()
    )  # type: ignore

    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv("MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets/providers"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    app = create_app()
    app.dependency_overrides[get_provider_set] = lambda: provider_set

    # find review_id via DB
    with factory() as session:
        from muzilla.db.models import ReviewBundle

        bundle = (
            session.query(ReviewBundle)
            .filter(ReviewBundle.scope_type == "track")
            .filter(ReviewBundle.scope_id == track_id)
            .one()
        )
        review_id = bundle.id

    with TestClient(app) as client:
        csrf = client.get("/api/auth/status").json()["csrf_token"]
        client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf})

        # without force -> 409
        resp = client.post(
            f"/api/reviews/{review_id}/candidates/import",
            json={"source": "musicbrainz", "ref_id": "release-1"},
        )
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "confirmation_required"

        # with force -> 200 and provider wins
        resp2 = client.post(
            f"/api/reviews/{review_id}/candidates/import",
            json={"source": "musicbrainz", "ref_id": "release-1", "force": True},
        )
        assert resp2.status_code == 200
        detail = resp2.json()
        # provenance preserved: title came from provider
        title_op = next(
            op for op in detail["current_revision"]["operations"] if op["field"] == "title"
        )
        assert title_op["proposed_value"] == "Provider Title"
        assert title_op["provenance"]["provider"] == "musicbrainz"
        # manual was overwritten, not duplicated
        titles = [op for op in detail["current_revision"]["operations"] if op["field"] == "title"]
        assert len(titles) == 1
