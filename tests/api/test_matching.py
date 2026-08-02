from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.api.deps import get_provider_set
from muzilla.config.loader import load_config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import CandidateUrlAlias, ProposalRevision, ReviewBundle, Track, TrackGroup
from muzilla.domain.reviews import BundleState
from muzilla.providers.base import (
    CandidateTrack,
    Capability,
    ProviderRef,
    ReleaseCandidate,
    ReleaseQuery,
)
from muzilla.services.providers import ProviderSet
from muzilla.services.reviews import OperationDraft, put_revision, transition_bundle


@dataclass
class StubProvider:
    releases: dict[str, ReleaseCandidate] = field(default_factory=dict)
    search_results: list[ReleaseCandidate] = field(default_factory=list)
    search_error: Exception | None = None
    get_release_error: Exception | None = None
    get_release_calls: list[ProviderRef] = field(default_factory=list)
    get_release_barrier: threading.Barrier | None = None
    track_candidates: dict[str, ReleaseCandidate] = field(default_factory=dict)
    get_track_error: Exception | None = None
    get_track_calls: list[ProviderRef] = field(default_factory=list)
    capabilities = frozenset({Capability.SEARCH_RELEASES, Capability.GET_RELEASE})

    async def search_releases(self, query: ReleaseQuery, limit: int) -> list[ReleaseCandidate]:
        if self.search_error is not None:
            raise self.search_error
        return self.search_results[:limit]

    async def get_release(self, ref: ProviderRef) -> ReleaseCandidate | None:
        self.get_release_calls.append(ref)
        if self.get_release_barrier is not None:
            await asyncio.to_thread(self.get_release_barrier.wait, 5)
        if self.get_release_error is not None:
            raise self.get_release_error
        return self.releases.get(ref.id)

    async def get_track_candidate(self, ref: ProviderRef) -> ReleaseCandidate | None:
        self.get_track_calls.append(ref)
        if self.get_track_error is not None:
            raise self.get_track_error
        return self.track_candidates.get(ref.id)


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
    release = _release()
    stub = StubProvider(
        releases={"release-1": release},
        search_results=[replace(release, tracks=(), track_count=1)],
    )
    return ProviderSet(metadata={"musicbrainz": stub}, art={}, lyrics={}, fingerprint={}, clients=())  # type: ignore[dict-item]


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


def _asgi_url_import_app(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_set: ProviderSet,
) -> FastAPI:
    """Build the review API without starting provider probes or the worker."""
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    app = create_app()
    app.state.config = load_config()
    app.dependency_overrides[get_provider_set] = lambda: provider_set
    return app


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


def _seed_track_review(db_path: Path) -> tuple[int, int]:
    track_id = _seed_track(db_path)
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        write = put_revision(
            session,
            logical_key=f"track:{track_id}",
            title="Review singleton.mp3",
            scope_type="track",
            scope_id=track_id,
            source_snapshot={"items": [{"source_type": "track", "source_id": track_id}]},
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=track_id,
                    current_value="Track One",
                    proposed_value="Track One",
                ),
            ),
        )
        transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)
        session.commit()
        return track_id, write.bundle_id


def test_get_group_candidates(matching_client: TestClient, migrated_db: Path) -> None:
    group_id, _ = _seed_group(migrated_db)
    resp = matching_client.get(f"/api/groups/{group_id}/candidates")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["source"] == "musicbrainz"
    assert body["candidates"][0]["track_count"] == 1


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


def test_manual_search_reports_results_and_not_configured_provider(
    matching_client: TestClient, migrated_db: Path
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)

    response = matching_client.post(
        f"/api/reviews/{review_id}/candidates/search",
        json={
            "title": " Track One ",
            "artist": "Test Artist",
            "providers": ["musicbrainz", "discogs"],
            "page_size": 5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["query"]["title"] == "Track One"
    assert [(outcome["provider"], outcome["status"]) for outcome in body["provider_outcomes"]] == [
        ("musicbrainz", "results"),
        ("discogs", "not_configured"),
    ]
    assert body["candidates"][0]["source"] == "musicbrainz"


def test_manual_candidate_import_is_idempotent_and_keeps_review_history(
    matching_client: TestClient, migrated_db: Path
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)

    first = matching_client.post(
        f"/api/reviews/{review_id}/candidates/import",
        json={"source": "musicbrainz", "ref_id": "release-1"},
    )
    second = matching_client.post(
        f"/api/reviews/{review_id}/candidates/import",
        json={"source": "musicbrainz", "ref_id": "release-1"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == review_id == second.json()["id"]
    assert first.json()["current_revision"]["candidate_source"] == "musicbrainz"
    assert first.json()["current_revision"]["candidate_ref"] == "release-1"
    assert first.json()["current_revision"]["id"] == second.json()["current_revision"]["id"]

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.query(ReviewBundle).count() == 1
        assert session.query(ProposalRevision).count() == 2


def test_manual_search_distinguishes_zero_results_from_provider_failure(
    matching_client: TestClient, migrated_db: Path, stub_provider_set: ProviderSet
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    provider.search_results = []

    zero = matching_client.post(
        f"/api/reviews/{review_id}/candidates/search",
        json={"title": "Track One", "providers": ["musicbrainz"]},
    )
    assert zero.status_code == 200
    assert zero.json()["provider_outcomes"] == [
        {"provider": "musicbrainz", "status": "zero_results", "result_count": 0, "detail": None}
    ]

    provider.search_error = RuntimeError("offline")
    failed = matching_client.post(
        f"/api/reviews/{review_id}/candidates/search",
        json={"title": "Track One", "providers": ["musicbrainz"]},
    )
    assert failed.status_code == 200
    assert failed.json()["provider_outcomes"] == [
        {"provider": "musicbrainz", "status": "failed", "result_count": 0, "detail": "search failed"}
    ]


def test_candidate_url_import_uses_only_recognized_provider_id_and_is_idempotent(
    matching_client: TestClient, migrated_db: Path, stub_provider_set: ProviderSet
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    url = "https://musicbrainz.org/release/00000000-0000-0000-0000-000000000001"
    provider.releases["00000000-0000-0000-0000-000000000001"] = replace(
        _release(),
        ref=ProviderRef(
            provider="musicbrainz", id="00000000-0000-0000-0000-000000000001"
        ),
        mb_release_id="00000000-0000-0000-0000-000000000001",
    )

    recognized = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/recognize", json={"url": url}
    )
    first = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
    )
    second = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
    )

    assert recognized.status_code == 200
    assert recognized.json() == {
        "provider": "musicbrainz",
        "candidate_type": "release",
        "provider_id": "00000000-0000-0000-0000-000000000001",
    }
    assert provider.get_release_calls == [
        ProviderRef(provider="musicbrainz", id="00000000-0000-0000-0000-000000000001"),
    ]
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["already_selected"] is False
    assert second.json()["already_selected"] is True
    assert first.json()["review"]["id"] == review_id == second.json()["review"]["id"]
    assert (
        first.json()["review"]["current_revision"]["id"]
        == second.json()["review"]["current_revision"]["id"]
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/private",
        "https://musicbrainz.org.evil.example/release/00000000-0000-0000-0000-000000000001",
        "file:///etc/passwd",
        "\x00https://musicbrainz.org/release/00000000-0000-0000-0000-000000000001",
        "https://musicbrainz.org/release/00000000-0000-0000-0000-0000000000\n01",
    ],
)
def test_candidate_url_rejects_ssrf_input_before_provider_fetch(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
    url: str,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)

    response = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
    )

    assert response.status_code == 400
    assert provider.get_release_calls == []


def test_deezer_track_url_duplicate_survives_provider_failure_without_refetch(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    candidate = replace(
        _release(),
        source="deezer",
        ref=ProviderRef(provider="deezer", id="302127"),
        candidate_type="track",
        mb_release_id=None,
        deezer_album_id="302127",
    )
    provider = StubProvider(
        track_candidates={"3135556": candidate},
    )
    provider.capabilities = frozenset(
        {Capability.SEARCH_RELEASES, Capability.GET_RELEASE, Capability.GET_TRACK}
    )
    stub_provider_set.metadata["deezer"] = provider  # type: ignore[assignment]
    url = "https://www.deezer.com/track/3135556"

    first = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
    )
    provider.get_track_error = httpx.ConnectError(
        "offline",
        request=httpx.Request("GET", "https://api.deezer.com/track/3135556"),
    )
    second = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
    )

    assert first.status_code == 200
    assert first.json()["already_selected"] is False
    assert second.status_code == 200
    assert second.json()["already_selected"] is True
    assert second.json()["review"]["current_revision"]["id"] == first.json()["review"][
        "current_revision"
    ]["id"]
    assert provider.get_track_calls == [
        ProviderRef(provider="deezer", id="3135556"),
    ]
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.query(CandidateUrlAlias).count() == 1


def test_candidate_url_alias_persists_identity_without_changing_revision_content(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    candidate = replace(
        _release(),
        source="deezer",
        ref=ProviderRef(provider="deezer", id="302127"),
        candidate_type="track",
        mb_release_id=None,
        deezer_album_id="302127",
    )
    provider = StubProvider(
        releases={"302127": replace(candidate, candidate_type="release")},
        track_candidates={"3135556": candidate},
    )
    provider.capabilities = frozenset(
        {Capability.SEARCH_RELEASES, Capability.GET_RELEASE, Capability.GET_TRACK}
    )
    stub_provider_set.metadata["deezer"] = provider  # type: ignore[assignment]
    album_url = "https://www.deezer.com/album/302127"
    track_url = "https://www.deezer.com/track/3135556"
    app = _asgi_url_import_app(migrated_db, tmp_path, monkeypatch, stub_provider_set)

    async def import_aliases() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return [
                await client.post(
                    f"/api/reviews/{review_id}/candidates/url/import", json={"url": album_url}
                ),
                await client.post(
                    f"/api/reviews/{review_id}/candidates/url/import", json={"url": track_url}
                ),
                await client.post(
                    f"/api/reviews/{review_id}/candidates/url/import", json={"url": track_url}
                ),
            ]

    selected_album, first_track, repeated_track = asyncio.run(import_aliases())

    assert [response.status_code for response in (selected_album, first_track, repeated_track)] == [
        200,
        200,
        200,
    ]
    album_revision = selected_album.json()["review"]["current_revision"]
    track_revision = first_track.json()["review"]["current_revision"]
    repeated_revision = repeated_track.json()["review"]["current_revision"]
    assert album_revision["id"] == track_revision["id"] == repeated_revision["id"]
    assert (
        album_revision["content_digest"]
        == track_revision["content_digest"]
        == repeated_revision["content_digest"]
    )
    assert provider.get_track_calls == [ProviderRef(provider="deezer", id="3135556")]
    assert "candidate_url_aliases" not in track_revision
    assert track_url not in str(first_track.json()["review"])
    assert "www.deezer.com" not in str(first_track.json()["review"])

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        aliases = session.query(CandidateUrlAlias).all()
        alias = next(alias for alias in aliases if alias.candidate_type == "track")
        revision = session.get(ProposalRevision, track_revision["id"])
        assert revision is not None
        assert (alias.provider, alias.candidate_type, alias.provider_id) == (
            "deezer",
            "track",
            "3135556",
        )
        assert revision.content_digest == track_revision["content_digest"]


def test_concurrent_same_url_import_keeps_one_alias_and_one_candidate_revision(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    ref_id = "00000000-0000-0000-0000-000000000001"
    provider.releases[ref_id] = replace(
        _release(),
        ref=ProviderRef(provider="musicbrainz", id=ref_id),
        mb_release_id=ref_id,
    )
    provider.get_release_barrier = threading.Barrier(2)
    url = f"https://musicbrainz.org/release/{ref_id}"
    app = _asgi_url_import_app(migrated_db, tmp_path, monkeypatch, stub_provider_set)

    async def import_twice() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await asyncio.gather(
                *(
                    client.post(
                        f"/api/reviews/{review_id}/candidates/url/import", json={"url": url}
                    )
                    for _ in range(2)
                )
            )

    responses = asyncio.run(import_twice())

    assert [response.status_code for response in responses] == [200, 200]
    assert provider.get_release_calls == [
        ProviderRef(provider="musicbrainz", id=ref_id),
        ProviderRef(provider="musicbrainz", id=ref_id),
    ]
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        revisions = session.query(ProposalRevision).all()
        assert len(revisions) == 2  # Initial revision plus one selected candidate.
        assert sum(revision.is_current for revision in revisions) == 1
        assert session.query(CandidateUrlAlias).count() == 1


def test_deezer_track_url_alias_is_remembered_when_album_is_already_selected(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    candidate = replace(
        _release(),
        source="deezer",
        ref=ProviderRef(provider="deezer", id="302127"),
        candidate_type="track",
        mb_release_id=None,
        deezer_album_id="302127",
    )
    provider = StubProvider(
        releases={"302127": replace(candidate, candidate_type="release")},
        track_candidates={"3135556": candidate},
    )
    provider.capabilities = frozenset(
        {Capability.SEARCH_RELEASES, Capability.GET_RELEASE, Capability.GET_TRACK}
    )
    stub_provider_set.metadata["deezer"] = provider  # type: ignore[assignment]
    album_url = "https://www.deezer.com/album/302127"
    track_url = "https://www.deezer.com/track/3135556"

    selected_album = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": album_url}
    )
    first_track = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": track_url}
    )
    provider.get_track_error = httpx.ConnectError(
        "offline",
        request=httpx.Request("GET", "https://api.deezer.com/track/3135556"),
    )
    repeated_track = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import", json={"url": track_url}
    )

    assert selected_album.status_code == 200
    assert selected_album.json()["already_selected"] is False
    assert first_track.status_code == 200
    assert first_track.json()["already_selected"] is True
    assert repeated_track.status_code == 200
    assert repeated_track.json()["already_selected"] is True
    assert provider.get_track_calls == [
        ProviderRef(provider="deezer", id="3135556"),
    ]
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.query(CandidateUrlAlias).count() == 2


def test_url_then_manual_import_of_same_candidate_keeps_revision_history(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    ref_id = "00000000-0000-0000-0000-000000000001"
    provider.releases[ref_id] = replace(
        _release(),
        ref=ProviderRef(provider="musicbrainz", id=ref_id),
        mb_release_id=ref_id,
    )

    from_url = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import",
        json={"url": f"https://musicbrainz.org/release/{ref_id}"},
    )
    from_manual_search = matching_client.post(
        f"/api/reviews/{review_id}/candidates/import",
        json={"source": "musicbrainz", "ref_id": ref_id},
    )

    assert from_url.status_code == 200
    assert from_manual_search.status_code == 200
    assert (
        from_manual_search.json()["current_revision"]["id"]
        == from_url.json()["review"]["current_revision"]["id"]
    )
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.query(ProposalRevision).count() == 2


@pytest.mark.parametrize(
    ("provider_error", "expected_status", "expected_code"),
    [
        (None, 404, "not_found"),
        (
            httpx.HTTPStatusError(
                "unauthorized",
                request=httpx.Request("GET", "https://provider.invalid/release/id"),
                response=httpx.Response(401),
            ),
            502,
            "invalid_credentials",
        ),
        (
            httpx.ConnectError(
                "offline",
                request=httpx.Request("GET", "https://provider.invalid/release/id"),
            ),
            503,
            "temporary_unavailable",
        ),
    ],
)
def test_candidate_url_reports_provider_fetch_outcomes(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
    provider_error: Exception | None,
    expected_status: int,
    expected_code: str,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    provider.get_release_error = provider_error

    response = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import",
        json={
            "url": "https://musicbrainz.org/release/00000000-0000-0000-0000-000000000099"
        },
    )

    assert response.status_code == expected_status
    assert response.json()["detail"]["code"] == expected_code


def test_candidate_url_reports_provider_not_configured_without_fetch(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    _track_id, review_id = _seed_track_review(migrated_db)
    provider = stub_provider_set.metadata.pop("musicbrainz")
    assert isinstance(provider, StubProvider)

    response = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import",
        json={
            "url": "https://musicbrainz.org/release/00000000-0000-0000-0000-000000000099"
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "not_configured"
    assert provider.get_release_calls == []
