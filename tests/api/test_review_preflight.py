from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import ID3
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from muzilla.api.app import create_app
from muzilla.db.engine import create_db_engine, create_session_factory
from muzilla.db.models import (
    ApplyRun,
    Job,
    Operation,
    OperationAttempt,
    ProposalRevision,
    ReviewFileJournal,
    Track,
)
from muzilla.domain.metadata import tag_hash
from muzilla.domain.reviews import BundleState
from muzilla.pipeline.reviews import (
    capture_refresh_source,
    observe_refresh_source,
    put_revision,
    refresh_review_bundle,
    transition_bundle,
)
from muzilla.services import jobs as jobs_service
from muzilla.services.reviews import OperationDraft
from muzilla.tags.hashing import partial_content_hash
from muzilla.tags.reader import read_track
from muzilla.tags.writer import write_fields

FIXTURE_AUDIO = Path(__file__).parent.parent / "fixtures" / "audio" / "silence.mp3"


def _configure_api(
    migrated_db: Path,
    library: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MUZILLA_STORAGE__DB_PATH", str(migrated_db))
    monkeypatch.setenv("MUZILLA_STORAGE__LIBRARY_ROOT", str(library))
    monkeypatch.setenv("MUZILLA_STORAGE__CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("MUZILLA_STORAGE__BLOB_DIR", str(tmp_path / "blobs"))
    monkeypatch.setenv(
        "MUZILLA_STORAGE__PROVIDER_SECRETS_DIR", str(tmp_path / "secrets" / "providers")
    )
    monkeypatch.setenv("MUZILLA_AUTH__ENABLED", "false")
    monkeypatch.setenv("MUZILLA_PROVIDERS_OFFLINE", "true")

    async def idle_worker_pool(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(jobs_service, "start_worker_pool", idle_worker_pool)


def _ready_review(
    session: Session,
    track: Track,
    logical_key: str,
    *,
    library_root: Path,
    source_items: list[dict[str, object]] | None = None,
    remove_art: bool = False,
    art_blob_id: int | None = None,
    write_lyrics: bool = False,
) -> int:
    stat = Path(track.path).stat()
    if source_items is None:
        from muzilla.changes.writer import capture_file_guard

        source_items = [
            {
                "source_type": "track",
                "source_id": track.id,
                "path": track.path,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "tag_hash": track.tag_hash,
                "physical_guard": capture_file_guard(Path(track.path), library_root),
            }
        ]
    assert not (remove_art and write_lyrics)
    if remove_art:
        assert art_blob_id is not None
        draft = OperationDraft(
            kind="remove_art",
            field="art",
            target_type="track",
            target_id=track.id,
            current_value={"blob_id": art_blob_id},
            proposed_value=None,
        )
    elif write_lyrics:
        draft = OperationDraft(
            kind="write_lyrics",
            field="lyrics",
            target_type="track",
            target_id=track.id,
            current_value={
                "text": "original lyrics payload",
                "synced": False,
                "provider": "legacy",
            },
            proposed_value={
                "text": "approved lyrics payload",
                "synced": False,
                "provider": "lrclib",
            },
        )
    else:
        draft = OperationDraft(
            kind="set_tag",
            field="title",
            target_type="track",
            target_id=track.id,
            current_value=track.title,
            proposed_value="Approved title",
        )
    write = put_revision(
        session,
        logical_key=logical_key,
        title="Preflight review",
        scope_type="track",
        scope_id=track.id,
        source_snapshot={"items": source_items},
        operations=(draft,),
    )
    operation = session.scalar(
        select(Operation).where(Operation.proposal_revision_id == write.revision_id)
    )
    assert operation is not None
    operation.decision = "accepted"
    transition_bundle(session, write.bundle_id, BundleState.READY)
    return write.bundle_id


def _seed_track(session: Session, source: Path) -> Track:
    metadata = read_track(source)
    stat = source.stat()
    track = Track(
        path=str(source),
        filename=source.name,
        ext=source.suffix,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        title=metadata.title or "Fixture title",
        artist=metadata.artist or "Fixture artist",
        tag_hash=tag_hash(metadata),
        has_embedded_art=metadata.has_embedded_art,
        has_lyrics=metadata.has_lyrics,
    )
    session.add(track)
    session.flush()
    return track


def _post_apply(client: TestClient, review_id: int) -> httpx.Response:
    csrf = client.get("/api/auth/status").json()["csrf_token"]
    return cast(
        httpx.Response,
        client.post(
            f"/api/reviews/{review_id}/apply",
            headers={
                "Origin": "http://testserver",
                "X-CSRF-Token": csrf,
                "Idempotency-Key": f"preflight-{review_id}",
            },
            json={"backup": False},
        ),
    )


def _assert_no_enqueue_or_file_journal(migrated_db: Path, source: Path, content: bytes) -> None:
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApplyRun)) == 0
        assert session.scalar(select(func.count()).select_from(OperationAttempt)) == 0
        assert session.scalar(select(func.count()).select_from(Job)) == 0
        assert session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 0
    engine.dispose()
    assert source.read_bytes() == content


def test_apply_api_rejects_stale_source_before_enqueue_or_write(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "stale.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(session, track, "preflight:stale", library_root=library)
        session.commit()
    engine.dispose()

    write_fields(source, {"title": "Changed outside review"})
    external_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, external_content)
    assert read_track(source).title == "Changed outside review"


@pytest.mark.parametrize(
    "source_items",
    [[], [{"source_id": 999}]],
    ids=["empty-snapshot", "omitted-target"],
)
def test_apply_api_rejects_incomplete_target_coverage_before_enqueue(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_items: list[dict[str, object]],
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "incomplete.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(
            session,
            track,
            "preflight:incomplete",
            library_root=library,
            source_items=source_items,
        )
        session.commit()
    engine.dispose()
    original_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, original_content)


def test_apply_api_rejects_legacy_snapshot_without_physical_guard_before_enqueue(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "legacy.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(
            session,
            track,
            "preflight:legacy-snapshot",
            library_root=library,
            source_items=[
                {
                    "source_type": "track",
                    "source_id": track.id,
                    "path": track.path,
                    "size_bytes": track.size_bytes,
                    "mtime_ns": track.mtime_ns,
                    "tag_hash": track.tag_hash,
                }
            ],
        )
        session.commit()
    engine.dispose()
    original_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, original_content)


def test_apply_api_rejects_equal_stat_non_tag_byte_drift_before_enqueue(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "physical-drift.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    original = source.read_bytes()
    id3_size = ID3(str(source)).size  # type: ignore[no-untyped-call]
    source.write_bytes(original[:id3_size] + original[id3_size:] * 24)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        original_tag_hash = track.tag_hash
        review_id = _ready_review(session, track, "preflight:physical-drift", library_root=library)
        session.commit()
    engine.dispose()

    original_stat = source.stat()
    original_partial_hash = partial_content_hash(source, original_stat.st_size)
    changed = bytearray(source.read_bytes())
    middle = len(changed) // 2
    assert middle > 64 * 1024 and middle < len(changed) - 64 * 1024
    changed[middle] ^= 1
    source.write_bytes(changed)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert partial_content_hash(source, original_stat.st_size) == original_partial_hash
    assert tag_hash(read_track(source)) == original_tag_hash
    changed_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, changed_content)


def test_apply_api_rejects_external_artwork_drift_before_enqueue(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mutagen.id3 import APIC

    library = tmp_path / "library"
    library.mkdir()
    source = library / "artwork-drift.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    original_art = b"original-art-payload-0001"
    changed_art = b"changed!-art-payload-0001"
    assert len(original_art) == len(changed_art)
    tags.add(
        APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=original_art)  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        from muzilla.changes.blobstore import BlobStore

        track = _seed_track(session, source)
        catalog_art = BlobStore(tmp_path / "blobs").put(
            session, b"synthetic art reference", mime="image/jpeg"
        )
        track.art_blob_id = catalog_art.id
        original_tag_hash = track.tag_hash
        review_id = _ready_review(
            session,
            track,
            "preflight:artwork-drift",
            library_root=library,
            remove_art=True,
            art_blob_id=catalog_art.id,
        )
        session.commit()
    engine.dispose()

    original_stat = source.stat()
    original_content = source.read_bytes()
    assert original_art in original_content
    changed_content = original_content.replace(original_art, changed_art)
    assert len(changed_content) == len(original_content)
    source.write_bytes(changed_content)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert tag_hash(read_track(source)) == original_tag_hash
    changed_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, changed_content)


def test_refresh_preserves_a_new_physical_guard_without_rewriting_history(
    migrated_db: Path, tmp_path: Path
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "refresh.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(session, track, "preflight:refresh-guard", library_root=library)
        session.commit()
        revision = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert revision is not None
        old_revision_id = revision.id
        old_snapshot = dict(revision.source_snapshot.payload)
        old_items = old_snapshot.get("items")
        assert isinstance(old_items, list) and isinstance(old_items[0], dict)
        old_guard = old_items[0].get("physical_guard")
        assert isinstance(old_guard, dict)

    write_fields(source, {"title": "Changed before explicit refresh"})
    with factory() as session:
        refresh_source = capture_refresh_source(session, review_id, library_root=library)
    observation = observe_refresh_source(refresh_source)
    with factory() as session:
        refresh_review_bundle(session, review_id, observation=observation)
        session.commit()
        old_revision = session.get(ProposalRevision, old_revision_id)
        current_revision = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert old_revision is not None and current_revision is not None
        assert old_revision.source_snapshot.payload == old_snapshot
        refreshed_items = current_revision.source_snapshot.payload.get("items")
        assert isinstance(refreshed_items, list) and isinstance(refreshed_items[0], dict)
        refreshed_guard = refreshed_items[0].get("physical_guard")
        assert isinstance(refreshed_guard, dict)
        assert refreshed_guard == observation.items[0]["physical_guard"]
        assert refreshed_guard != old_guard
    engine.dispose()


def test_refreshed_no_result_review_can_import_candidate_and_apply(
    migrated_db: Path, tmp_path: Path
) -> None:
    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.changes.writer import capture_file_guard
    from muzilla.config.schema import PathsConfig
    from muzilla.db.models import ReviewBundle
    from muzilla.pipeline.proposals import ProposalComposer
    from muzilla.providers.base import CandidateTrack, ProviderRef, ReleaseCandidate
    from muzilla.services.review_apply import enqueue_review_apply

    library = tmp_path / "library"
    library.mkdir()
    source = library / "no-result.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        stat = source.stat()
        write = put_revision(
            session,
            logical_key=f"track:{track.id}",
            title="No result",
            scope_type="track",
            scope_id=track.id,
            source_snapshot={
                "items": [
                    {
                        "source_type": "track",
                        "source_id": track.id,
                        "path": track.path,
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "tag_hash": track.tag_hash,
                        "filename": track.filename,
                        "physical_guard": capture_file_guard(source, library),
                    }
                ]
            },
            operations=(),
            match_explanation={"outcome": "zero_results"},
        )
        transition_bundle(session, write.bundle_id, BundleState.NEEDS_ATTENTION)
        session.commit()
        review_id = write.bundle_id
        old_revision_id = write.revision_id
        track_id = track.id

    with factory() as session:
        refresh_source = capture_refresh_source(session, review_id, library_root=library)
    observation = observe_refresh_source(refresh_source)
    with factory() as session:
        refresh_review_bundle(session, review_id, observation=observation)
        session.commit()
        old_revision = session.get(ProposalRevision, old_revision_id)
        review = session.get(ReviewBundle, review_id)
        assert old_revision is not None and review is not None
        current_revision = session.scalar(
            select(ProposalRevision).where(
                ProposalRevision.review_bundle_id == review_id,
                ProposalRevision.is_current.is_(True),
            )
        )
        assert current_revision is not None
        old_items = old_revision.source_snapshot.payload["items"]
        current_items = current_revision.source_snapshot.payload["items"]
        assert isinstance(old_items, list) and isinstance(old_items[0], dict)
        assert isinstance(current_items, list) and isinstance(current_items[0], dict)
        assert isinstance(old_items[0].get("physical_guard"), dict)
        assert current_items[0].get("physical_guard") == observation.items[0]["physical_guard"]

        candidate = ReleaseCandidate(
            source="musicbrainz",
            ref=ProviderRef(provider="musicbrainz", id="release-1"),
            album="Album",
            album_artist="Artist",
            tracks=(CandidateTrack(position=1, title="New title", artist="New artist"),),
        )
        detail = ProposalComposer(
            session, paths_config=PathsConfig(default="$artist - $title")
        ).compose_candidate(review, candidate)
        revision = session.get(ProposalRevision, detail.current_revision.id)
        assert revision is not None
        for operation in revision.operations:
            operation.decision = "accepted"
        session.commit()

        enqueued = enqueue_review_apply(
            session, review_id, idempotency_key="refreshed-candidate", library_root=library
        )
        result = apply_review_run(
            session,
            enqueued.apply_run_id,
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )
        applied_track = session.get(Track, track_id)
        assert applied_track is not None
        destination = Path(applied_track.path)
        assert result.state == "applied"
        assert destination != source and destination.is_file()
        assert read_track(destination).title == "New title"
    engine.dispose()


def test_apply_api_rejects_equal_stat_lyrics_drift_before_enqueue(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mutagen.id3 import USLT

    library = tmp_path / "library"
    library.mkdir()
    source = library / "lyrics-drift.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    tags = ID3(str(source))  # type: ignore[no-untyped-call]
    original_lyrics = "original lyrics payload"
    changed_lyrics = "changed! lyrics payload"
    assert len(original_lyrics) == len(changed_lyrics)
    tags.add(
        USLT(encoding=3, lang="eng", desc="", text=original_lyrics)  # type: ignore[no-untyped-call]
    )
    tags.save(str(source))
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        original_tag_hash = track.tag_hash
        review_id = _ready_review(
            session,
            track,
            "preflight:lyrics-drift",
            library_root=library,
            write_lyrics=True,
        )
        session.commit()
    engine.dispose()

    original_stat = source.stat()
    original_content = source.read_bytes()
    original_bytes = original_lyrics.encode("latin-1")
    changed_bytes = changed_lyrics.encode("latin-1")
    assert original_bytes in original_content and len(original_bytes) == len(changed_bytes)
    changed_content = original_content.replace(original_bytes, changed_bytes)
    assert len(changed_content) == len(original_content)
    source.write_bytes(changed_content)
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert source.stat().st_size == original_stat.st_size
    assert source.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert tag_hash(read_track(source)) == original_tag_hash
    changed_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_source"
    _assert_no_enqueue_or_file_journal(migrated_db, source, changed_content)


def test_apply_api_rejects_another_active_review_for_same_file(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "shared.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        _ready_review(session, track, "preflight:shared:first", library_root=library)
        review_id = _ready_review(session, track, "preflight:shared:second", library_root=library)
        session.commit()
    engine.dispose()
    original_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "concurrent_conflict"
    _assert_no_enqueue_or_file_journal(migrated_db, source, original_content)


@pytest.mark.parametrize(
    "competing_source_items",
    [[], [{}]],
    ids=["empty-source-list", "malformed-source-item"],
)
def test_apply_api_rejects_competing_review_without_verifiable_target_coverage(
    migrated_db: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    competing_source_items: list[dict[str, object]],
) -> None:
    library = tmp_path / "library"
    library.mkdir()
    source = library / "shared-incomplete.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        _ready_review(
            session,
            track,
            "preflight:shared-incomplete:first",
            library_root=library,
            source_items=competing_source_items,
        )
        review_id = _ready_review(
            session, track, "preflight:shared-incomplete:second", library_root=library
        )
        session.commit()
    engine.dispose()
    original_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "concurrent_conflict"
    _assert_no_enqueue_or_file_journal(migrated_db, source, original_content)


def test_apply_api_rejects_active_review_without_current_revision_before_enqueue(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muzilla.db.models import ReviewBundle

    library = tmp_path / "library"
    library.mkdir()
    source = library / "missing-revision.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(
            session, track, "preflight:missing-revision:requested", library_root=library
        )
        session.add(
            ReviewBundle(
                logical_key=f"track:{track.id}:missing-revision:competing",
                title="Incomplete active review",
                scope_type="track",
                scope_id=track.id,
                state=BundleState.READY.value,
            )
        )
        session.commit()
    engine.dispose()
    original_content = source.read_bytes()

    with TestClient(create_app()) as client:
        response = _post_apply(client, review_id)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "concurrent_conflict"
    _assert_no_enqueue_or_file_journal(migrated_db, source, original_content)


def test_repeated_apply_request_after_success_returns_the_original_job(
    migrated_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import apply_review_run

    library = tmp_path / "library"
    library.mkdir()
    source = library / "applied.mp3"
    shutil.copy(FIXTURE_AUDIO, source)
    _configure_api(migrated_db, library, tmp_path, monkeypatch)

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        track = _seed_track(session, source)
        review_id = _ready_review(session, track, "preflight:idempotent", library_root=library)
        session.commit()
    engine.dispose()

    with TestClient(create_app()) as client:
        first = _post_apply(client, review_id)
    assert first.status_code == 202
    first_result = first.json()

    engine = create_db_engine(migrated_db)
    factory = create_session_factory(engine)
    with factory() as session:
        result = apply_review_run(
            session,
            first_result["apply_run_id"],
            library_root=library,
            blob_store=BlobStore(tmp_path / "blobs"),
        )
    assert result.state == "applied"
    applied_content = source.read_bytes()

    with TestClient(create_app()) as client:
        repeated = _post_apply(client, review_id)

    assert repeated.status_code == 202
    assert repeated.json() == first_result
    assert source.read_bytes() == applied_content
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ApplyRun)) == 1
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(ReviewFileJournal)) == 1
    engine.dispose()
