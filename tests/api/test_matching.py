from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.api.deps import get_provider_set
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import Track, TrackGroup
from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate, ReleaseQuery
from muzilla.services.providers import ProviderSet


@dataclass
class StubProvider:
    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    search_results: list[ReleaseCandidate] = field(default_factory=list)

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        return self.search_results[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        return self.releases.get(ref.id)


def _release() -> ReleaseCandidate:
    return ReleaseCandidate(
        source="musicbrainz",
        ref=ProviderRef(provider="musicbrainz", id="release-1"),
        album="Test Album",
        album_artist="Test Artist",
        year=2000,
        mb_release_id="release-1",
        tracks=(CandidateTrack(position=1, title="Track One", duration_ms=100_000),),
    )


@pytest.fixture
def stub_provider_set() -> ProviderSet:
    stub = StubProvider(releases={"release-1": _release()}, search_results=[_release()])
    return ProviderSet(metadata={"musicbrainz": stub}, art={}, lyrics={}, fingerprint={}, clients=())  # type: ignore[arg-type]


@pytest.fixture
def matching_client(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_provider_set: ProviderSet,
) -> Iterator[TestClient]:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    app = create_app()
    app.dependency_overrides[get_provider_set] = lambda: stub_provider_set
    with TestClient(app) as c:
        yield c


def _seed_group(db_path: Path) -> tuple[int, int]:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        group = TrackGroup(key="test-group", album="Test Album", album_artist="Test Artist")
        session.add(group)
        session.flush()
        t = Track(
            path="/music/a.mp3", filename="a.mp3", ext="mp3", size_bytes=1, mtime_ns=1,
            title="Track One", album="Test Album", album_artist="Test Artist",
            duration_ms=100_000, group_id=group.id,
            first_seen_at=now, last_scanned_at=now,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        session.refresh(group)
        return group.id, t.id


def _seed_track(db_path: Path) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        t = Track(
            path="/music/singleton.mp3", filename="singleton.mp3", ext="mp3",
            size_bytes=1, mtime_ns=1, title="Track One", duration_ms=100_000,
            first_seen_at=now, last_scanned_at=now,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def test_get_group_candidates(matching_client: TestClient, migrated_db: Path) -> None:
    group_id, _ = _seed_group(migrated_db)
    resp = matching_client.get(f"/api/groups/{group_id}/candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["source"] == "musicbrainz"


def test_get_group_candidates_404_for_unknown_group(matching_client: TestClient) -> None:
    resp = matching_client.get("/api/groups/99999/candidates")
    assert resp.status_code == 404


def test_stage_group_creates_changeset(matching_client: TestClient, migrated_db: Path) -> None:
    group_id, _track_id = _seed_group(migrated_db)
    resp = matching_client.post(
        f"/api/groups/{group_id}/stage", json={"source": "musicbrainz", "ref_id": "release-1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "match_proposal"
    assert body["candidate_source"] == "musicbrainz"
    assert body["candidate_ref"] == "release-1"


def test_stage_group_400_for_unknown_release(matching_client: TestClient, migrated_db: Path) -> None:
    group_id, _ = _seed_group(migrated_db)
    resp = matching_client.post(
        f"/api/groups/{group_id}/stage", json={"source": "musicbrainz", "ref_id": "nope"}
    )
    assert resp.status_code == 400


def test_get_track_candidates(matching_client: TestClient, migrated_db: Path) -> None:
    track_id = _seed_track(migrated_db)
    resp = matching_client.get(f"/api/tracks/{track_id}/candidates")
    assert resp.status_code == 200
    assert len(resp.json()["candidates"]) == 1


def test_stage_track_creates_changeset(matching_client: TestClient, migrated_db: Path) -> None:
    track_id = _seed_track(migrated_db)
    resp = matching_client.post(
        f"/api/tracks/{track_id}/stage", json={"source": "musicbrainz", "ref_id": "release-1"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scope_type"] == "track"
    assert body["scope_id"] == track_id
