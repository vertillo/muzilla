from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from muzilla.api.app import create_app
from muzilla.api.deps import get_provider_set
from muzilla.config.loader import load_config
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import CandidateUrlAlias, ProposalRevision, ReviewBundle, Track, WorkUnit
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
    return ProviderSet(
        metadata=cast(Any, {"musicbrainz": stub}), art={}, lyrics={}, fingerprint={}, clients=()
    )


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


def _seed_track(
    db_path: Path,
    *,
    filename: str = "singleton.mp3",
    title: str | None = "Track One",
    artist: str | None = None,
) -> int:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        t = Track(
            path=f"/music/{filename}",
            filename=filename,
            ext="mp3",
            size_bytes=1,
            mtime_ns=1,
            title=title,
            artist=artist,
            duration_ms=100_000,
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(t)
        session.commit()
        session.refresh(t)
        return t.id


def _seed_track_review(
    db_path: Path,
    *,
    filename: str = "singleton.mp3",
    title: str | None = "Track One",
    artist: str | None = None,
) -> tuple[int, int]:
    track_id = _seed_track(db_path, filename=filename, title=title, artist=artist)
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    with factory() as session:
        write = put_revision(
            session,
            logical_key=f"track:{track_id}",
            title=f"Review {filename}",
            scope_type="track",
            scope_id=track_id,
            source_snapshot={"items": [{"source_type": "track", "source_id": track_id}]},
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=track_id,
                    current_value=title,
                    proposed_value=title or "Track One",
                ),
            ),
        )
        transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)
        session.commit()
        return track_id, write.bundle_id


def _seed_group_review(db_path: Path, *, barcode: str | None = None) -> tuple[int, int]:
    engine = create_db_engine(db_path)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    with factory() as session:
        group = WorkUnit(
            key="matching-group",
            kind="album",
            album="Test Album",
            album_artist="Test Artist",
            barcode=barcode,
        )
        session.add(group)
        session.flush()
        track = Track(
            path="/music/group-track.mp3",
            filename="group-track.mp3",
            ext="mp3",
            size_bytes=1,
            mtime_ns=1,
            title="Track One",
            album="Test Album",
            album_artist="Test Artist",
            duration_ms=100_000,
            work_unit_id=group.id,
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.flush()
        write = put_revision(
            session,
            logical_key=f"group:{group.id}",
            title="Review album",
            scope_type="group",
            scope_id=group.id,
            source_snapshot={"items": [{"source_type": "track", "source_id": track.id}]},
            operations=(
                OperationDraft(
                    kind="set_tag",
                    field="title",
                    target_type="track",
                    target_id=track.id,
                    current_value="Track One",
                    proposed_value="Track One",
                ),
            ),
        )
        transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)
        session.commit()
        return group.id, write.bundle_id


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
    # legacy ChangeSet staging removed per COMPAT-CHANGESET-001 / migration 0019:
    # the /stage route no longer exists, so nothing is staged. Observed status
    # depends on the packaged UI: 404 without it, 405 with it (the GET-only
    # SPA catch-all matches the path but not POST). Both prove the route is gone.
    assert resp.status_code in (404, 405)


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


def test_track_candidate_selection_observes_inert_placeholder(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_provider_set: ProviderSet,
) -> None:
    import shutil

    from sqlalchemy import select

    from muzilla.changes.writer import capture_file_guard
    from muzilla.domain.metadata import tag_hash
    from muzilla.tags.hashing import partial_content_hash
    from muzilla.tags.reader import read_track

    library = tmp_path / "library"
    library.mkdir()
    source = library / "track.mp3"
    shutil.copy(Path("tests/fixtures/audio/silence.mp3"), source)
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    metadata = read_track(source)
    stat = source.stat()
    now = datetime.now(UTC)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = Track(
            path=str(source),
            filename=source.name,
            ext=source.suffix,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=partial_content_hash(source, stat.st_size),
            tag_hash=tag_hash(metadata),
            title="Track One",
            duration_ms=100_000,
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.flush()
        placeholder = put_revision(
            session,
            logical_key=f"track:{track.id}",
            title="Prepared review",
            scope_type="track",
            scope_id=track.id,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": track.id,
                        "path": track.path,
                        "filename": track.filename,
                        "size_bytes": track.size_bytes,
                        "mtime_ns": track.mtime_ns,
                        "content_hash": track.content_hash,
                        "tag_hash": track.tag_hash,
                        "art_blob_id": track.art_blob_id,
                    }
                ]
            },
            operations=(),
        )
        track_id = track.id
        review_id = placeholder.bundle_id
        session.commit()

    app = _asgi_url_import_app(migrated_db, tmp_path, monkeypatch, stub_provider_set)

    async def select_candidate() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(
                f"/api/tracks/{track_id}/review/candidate",
                json={"source": "musicbrainz", "ref_id": "release-1"},
            )

    response = asyncio.run(select_candidate())
    assert response.status_code == 200, response.text
    assert response.json()["id"] == review_id
    assert response.json()["current_revision"]["revision_no"] == 2
    with factory() as session:
        current = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert current is not None
        items = current.source_snapshot.payload.get("items")
        assert isinstance(items, list) and len(items) == 1
        assert isinstance(items[0], dict)
        assert items[0].get("physical_guard") == capture_file_guard(source, library)
    engine.dispose()


@pytest.mark.parametrize("selection", ["id", "url"])
@pytest.mark.parametrize("has_inert_revision", [False, True], ids=["no-revision", "inert-revision"])
def test_manual_candidate_import_observes_inert_placeholder(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stub_provider_set: ProviderSet,
    selection: str,
    has_inert_revision: bool,
) -> None:
    import os
    import shutil

    from sqlalchemy import select

    from muzilla.changes.writer import capture_file_guard
    from muzilla.domain.metadata import tag_hash
    from muzilla.tags.hashing import partial_content_hash
    from muzilla.tags.reader import read_track

    library = tmp_path / "library"
    library.mkdir()
    source = library / "manual.mp3"
    shutil.copy(Path("tests/fixtures/audio/silence.mp3"), source)
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    metadata = read_track(source)
    stat = source.stat()
    now = datetime.now(UTC)
    ref_id = "00000000-0000-0000-0000-000000000001"
    candidate = replace(
        _release(),
        ref=ProviderRef(provider="musicbrainz", id=ref_id),
        mb_release_id=ref_id,
    )
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    provider.releases[ref_id] = candidate

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = Track(
            path=str(source),
            filename=source.name,
            ext=source.suffix,
            size_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            content_hash=partial_content_hash(source, stat.st_size),
            tag_hash=tag_hash(metadata),
            title="Track One",
            artist=None,
            duration_ms=100_000,
            first_seen_at=now,
            last_scanned_at=now,
        )
        session.add(track)
        session.flush()
        if has_inert_revision:
            placeholder = put_revision(
                session,
                logical_key=f"track:{track.id}",
                title="Manual candidate review",
                scope_type="track",
                scope_id=track.id,
                source_snapshot={
                    "items": [
                        {
                            "source_type": "track",
                            "source_id": track.id,
                            "path": track.path,
                            "filename": track.filename,
                            "size_bytes": track.size_bytes,
                            "mtime_ns": track.mtime_ns,
                            "content_hash": track.content_hash,
                            "tag_hash": track.tag_hash,
                            "art_blob_id": track.art_blob_id,
                        }
                    ]
                },
                operations=(),
            )
            review_id = placeholder.bundle_id
            placeholder_revision_id = placeholder.revision_id
        else:
            review = ReviewBundle(
                logical_key=f"track:{track.id}",
                title="Manual candidate review",
                scope_type="track",
                scope_id=track.id,
                state=BundleState.PREPARING.value,
            )
            session.add(review)
            session.flush()
            review_id = review.id
            placeholder_revision_id = None
        session.commit()

    app = _asgi_url_import_app(migrated_db, tmp_path, monkeypatch, stub_provider_set)

    async def choose_candidate() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            if selection == "id":
                return await client.post(
                    f"/api/reviews/{review_id}/candidates/import",
                    json={"source": "musicbrainz", "ref_id": ref_id},
                )
            return await client.post(
                f"/api/reviews/{review_id}/candidates/url/import",
                json={"url": f"https://musicbrainz.org/release/{ref_id}"},
            )

    response = asyncio.run(choose_candidate())
    assert response.status_code == 200, response.text
    review_out = response.json()["review"] if selection == "url" else response.json()
    assert review_out["id"] == review_id
    assert review_out["current_revision"]["revision_no"] == (2 if has_inert_revision else 1)

    with factory() as session:
        current = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert current is not None
        if has_inert_revision:
            assert placeholder_revision_id is not None
            placeholder_revision = session.get(ProposalRevision, placeholder_revision_id)
            assert placeholder_revision is not None
            assert current.id != placeholder_revision.id
            placeholder_items = placeholder_revision.source_snapshot.payload.get("items")
            assert isinstance(placeholder_items, list) and len(placeholder_items) == 1
            assert isinstance(placeholder_items[0], dict)
            assert "physical_guard" not in placeholder_items[0]
        items = current.source_snapshot.payload.get("items")
        assert isinstance(items, list) and len(items) == 1
        assert isinstance(items[0], dict)
        assert items[0]["path"] == str(source)
        assert items[0]["size_bytes"] == stat.st_size
        assert items[0]["mtime_ns"] == stat.st_mtime_ns
        assert items[0]["tag_hash"] == tag_hash(metadata)
        preview_guard = items[0].get("physical_guard")
        assert isinstance(preview_guard, dict)
        assert preview_guard == capture_file_guard(source, library)

    content = bytearray(source.read_bytes())
    content[len(content) // 2] ^= 1
    source.write_bytes(content)
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    changed_guard = capture_file_guard(source, library)
    assert changed_guard != preview_guard
    second_ref_id = "00000000-0000-0000-0000-000000000002"
    provider.releases[second_ref_id] = replace(
        candidate,
        ref=ProviderRef(provider="musicbrainz", id=second_ref_id),
        mb_release_id=second_ref_id,
    )

    async def select_second_candidate() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            if selection == "id":
                return await client.post(
                    f"/api/reviews/{review_id}/candidates/import",
                    json={"source": "musicbrainz", "ref_id": second_ref_id},
                )
            return await client.post(
                f"/api/reviews/{review_id}/candidates/url/import",
                json={"url": f"https://musicbrainz.org/release/{second_ref_id}"},
            )

    second_response = asyncio.run(select_second_candidate())
    assert second_response.status_code == 200, second_response.text
    with factory() as session:
        current_after = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert current_after is not None
        after_items = current_after.source_snapshot.payload.get("items")
        assert isinstance(after_items, list) and len(after_items) == 1
        assert isinstance(after_items[0], dict)
        assert after_items[0].get("physical_guard") == preview_guard
        assert after_items[0].get("physical_guard") != changed_guard
    engine.dispose()


@pytest.mark.parametrize("scope_type", ["track", "group"])
def test_persisted_reject_threshold_hides_and_blocks_forced_manual_selection(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
    scope_type: str,
) -> None:
    if scope_type == "track":
        _scope_id, review_id = _seed_track_review(migrated_db)
        query = {"title": "Track One", "providers": ["musicbrainz"]}
        reject_threshold = 0.001
        candidate_title = "Track On"
        candidate_barcode = None
    else:
        _scope_id, review_id = _seed_group_review(migrated_db, barcode="local-barcode")
        query = {"album": "Test Album", "providers": ["musicbrainz"]}
        reject_threshold = 0.1
        candidate_title = "Track One"
        candidate_barcode = "candidate-barcode"
    provider = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(provider, StubProvider)
    ref_id = "00000000-0000-0000-0000-000000000001"
    candidate = replace(
        _release(),
        ref=ProviderRef(provider="musicbrainz", id=ref_id),
        mb_release_id=ref_id,
        barcode=candidate_barcode,
        tracks=(CandidateTrack(position=1, title=candidate_title, duration_ms=100_000),),
    )
    provider.releases[ref_id] = candidate
    provider.search_results = [replace(candidate, tracks=(), track_count=1)]
    original_revision = matching_client.get(f"/api/reviews/{review_id}").json()["current_revision"]

    before = matching_client.post(f"/api/reviews/{review_id}/candidates/search", json=query)
    assert before.status_code == 200
    assert len(before.json()["candidates"]) == 1

    csrf_token = matching_client.get("/api/auth/status").json()["csrf_token"]
    matching_client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf_token})
    updated = matching_client.put(
        "/api/settings/matching",
        json={
            "album_strong_threshold": 0.0,
            "album_reject_threshold": reject_threshold,
            "singleton_strong_threshold": 0.0,
            "singleton_reject_threshold": 0.001,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["album_reject_threshold"] == reject_threshold
    assert updated.json()["singleton_reject_threshold"] == 0.001
    persisted = matching_client.get("/api/settings").json()["matching"]
    assert persisted["album_reject_threshold"] == reject_threshold
    assert persisted["singleton_reject_threshold"] == 0.001

    hidden = matching_client.post(f"/api/reviews/{review_id}/candidates/search", json=query)
    forced_import = matching_client.post(
        f"/api/reviews/{review_id}/candidates/import",
        json={"source": "musicbrainz", "ref_id": ref_id, "force": True},
    )
    forced_url_import = matching_client.post(
        f"/api/reviews/{review_id}/candidates/url/import",
        json={"url": f"https://musicbrainz.org/release/{ref_id}", "force": True},
    )

    assert hidden.status_code == 200
    assert hidden.json()["candidates"] == []
    assert forced_import.status_code == 400
    assert forced_url_import.status_code == 400
    unchanged = matching_client.get(f"/api/reviews/{review_id}")
    assert unchanged.status_code == 200
    unchanged_revision = unchanged.json()["current_revision"]
    assert unchanged_revision["candidate_source"] is None
    assert unchanged_revision["id"] == original_revision["id"]
    assert unchanged_revision["operations"] == original_revision["operations"]


def test_manual_candidate_import_uses_filename_matching_evidence(
    matching_client: TestClient, migrated_db: Path
) -> None:
    _track_id, review_id = _seed_track_review(
        migrated_db,
        filename="Test Artist - Track One.mp3",
        title=None,
        artist=None,
    )
    query = {
        "title": "Track One",
        "artist": "Test Artist",
        "providers": ["musicbrainz"],
    }

    searched = matching_client.post(f"/api/reviews/{review_id}/candidates/search", json=query)
    assert searched.status_code == 200
    assert len(searched.json()["candidates"]) == 1

    imported = matching_client.post(
        f"/api/reviews/{review_id}/candidates/import",
        json={"source": "musicbrainz", "ref_id": "release-1"},
    )
    assert imported.status_code == 200
    assert imported.json()["current_revision"]["candidate_source"] == "musicbrainz"
    assert imported.json()["current_revision"]["candidate_ref"] == "release-1"


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


def test_deezer_track_url_cache_imports_same_candidate_in_distinct_reviews(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    _first_track_id, first_review_id = _seed_track_review(migrated_db)
    _second_track_id, second_review_id = _seed_track_review(
        migrated_db, filename="singleton-second.mp3"
    )
    candidate = replace(
        _release(),
        source="deezer",
        ref=ProviderRef(provider="deezer", id="302127"),
        candidate_type="track",
        representative_track=CandidateTrack(
            position=1, title="Track One", artist="Test Artist", duration_ms=100_000
        ),
        mb_release_id=None,
        deezer_album_id="302127",
    )
    provider = StubProvider(track_candidates={"3135556": candidate})
    provider.capabilities = frozenset(
        {Capability.SEARCH_RELEASES, Capability.GET_RELEASE, Capability.GET_TRACK}
    )
    stub_provider_set.metadata["deezer"] = provider  # type: ignore[assignment]
    url = "https://www.deezer.com/track/3135556"

    first = matching_client.post(
        f"/api/reviews/{first_review_id}/candidates/url/import", json={"url": url}
    )
    second = matching_client.post(
        f"/api/reviews/{second_review_id}/candidates/url/import", json={"url": url}
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["already_selected"] is False
    assert second.json()["already_selected"] is False
    first_revision = first.json()["review"]["current_revision"]
    second_revision = second.json()["review"]["current_revision"]
    assert (first_revision["candidate_source"], first_revision["candidate_ref"]) == (
        "deezer",
        "302127",
    )
    assert (second_revision["candidate_source"], second_revision["candidate_ref"]) == (
        "deezer",
        "302127",
    )
    assert [op["proposed_value"] for op in first_revision["operations"]] == [
        op["proposed_value"] for op in second_revision["operations"]
    ]
    assert provider.get_track_calls == [ProviderRef(provider="deezer", id="3135556")]


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


def test_get_track_candidates_persists_hydrate_cache_across_requests(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    """Interactive matching must persist provider-cache writes.

    The endpoint session never committed, so cache_put flushes were rolled
    back when the request session closed: every repeat hydrate re-fetched
    over the network and re-paid the provider rate limiter (observed at
    100k scale: 30 identical hydrates, zero cache reuse, ~2s per call
    against the 1 req/s MusicBrainz politeness floor).
    """
    from sqlalchemy import select

    from muzilla.db.models import ProviderCache

    track_id = _seed_track(migrated_db)
    stub = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(stub, StubProvider)

    first = matching_client.get(f"/api/tracks/{track_id}/candidates")
    assert first.status_code == 200
    assert len(first.json()["candidates"]) == 1
    assert len(stub.get_release_calls) == 1

    second = matching_client.get(f"/api/tracks/{track_id}/candidates")
    assert second.status_code == 200
    # hydrate served from the persistent cache: no second provider fetch
    assert len(stub.get_release_calls) == 1
    assert second.json()["candidates"] == first.json()["candidates"]

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        rows = session.execute(select(ProviderCache)).scalars().all()
        assert any(row.operation == "get_release" for row in rows)


def test_manual_search_persists_hydrate_cache_across_requests(
    matching_client: TestClient,
    migrated_db: Path,
    stub_provider_set: ProviderSet,
) -> None:
    """Manual candidate search shares the same persistent-cache fix."""
    _track_id, review_id = _seed_track_review(migrated_db)
    stub = stub_provider_set.metadata["musicbrainz"]
    assert isinstance(stub, StubProvider)
    payload = {
        "title": "Track One",
        "artist": "Test Artist",
        "providers": ["musicbrainz"],
        "page_size": 5,
    }

    first = matching_client.post(f"/api/reviews/{review_id}/candidates/search", json=payload)
    assert first.status_code == 200
    hydrate_calls_after_first = len(stub.get_release_calls)
    assert hydrate_calls_after_first >= 1

    second = matching_client.post(f"/api/reviews/{review_id}/candidates/search", json=payload)
    assert second.status_code == 200
    assert len(stub.get_release_calls) == hydrate_calls_after_first
