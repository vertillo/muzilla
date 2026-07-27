from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from muzilla.changes.applier import apply_changeset
from muzilla.changes.blobstore import BlobStore
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.changes.undo import build_undo_changeset
from muzilla.db.models import ApplyJournal, Blob, ChangeSet, Track
from muzilla.pipeline.scan import scan_library
from muzilla.tags.reader import read_track

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"

_JPEG_A = b"\xff\xd8\xff\xe0" + b"cover art A" * 20
_JPEG_B = b"\xff\xd8\xff\xe0" + b"cover art B" * 20


def _scan_one(db_session: Session, tmp_path: Path, name: str = "silence.mp3") -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / name, library / name)
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).filter(Track.filename == name).one()


def _stage_embed_art(
    db_session: Session, track: Track, blob_id: int | None, *, title: str = "Embed art"
) -> ChangeSet:
    cs = build_changeset(
        db_session,
        title=title,
        source="enrichment",
        edits={track.id: [FieldEdit(field="art", new_value=None, op="embed_art", new_blob_id=blob_id)]},
    )
    db_session.commit()
    return cs


def test_apply_embeds_art_into_the_file_and_sets_track_columns(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    assert track.has_embedded_art is False
    assert track.art_blob_id is None

    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, _JPEG_A, mime="image/jpeg", width=500, height=500)
    db_session.commit()

    cs = _stage_embed_art(db_session, track, blob.id)

    result = apply_changeset(db_session, cs.id, blob_store=store)
    db_session.commit()

    assert result.state == "applied"
    db_session.refresh(track)
    assert track.has_embedded_art is True
    assert track.art_blob_id == blob.id

    on_disk = read_track(Path(track.path))
    assert on_disk.has_embedded_art is True

    refreshed_blob = db_session.get(Blob, blob.id)
    assert refreshed_blob is not None
    assert refreshed_blob.refcount == 1


def test_apply_without_blob_store_fails_the_track(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = _stage_embed_art(db_session, track, blob_id=999)

    result = apply_changeset(db_session, cs.id)  # no blob_store
    db_session.commit()

    assert result.state == "failed"
    assert track.id in result.conflicted_track_ids
    on_disk = read_track(Path(track.path))
    assert on_disk.has_embedded_art is False


def test_re_embedding_releases_old_blob_and_retains_new(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    store = BlobStore(tmp_path / "blobs")
    blob_a = store.put(db_session, _JPEG_A, mime="image/jpeg")
    blob_b = store.put(db_session, _JPEG_B, mime="image/jpeg")
    db_session.commit()

    cs1 = _stage_embed_art(db_session, track, blob_a.id)
    apply_changeset(db_session, cs1.id, blob_store=store)
    db_session.commit()
    db_session.refresh(track)
    assert track.art_blob_id == blob_a.id
    assert db_session.get(Blob, blob_a.id).refcount == 1  # type: ignore[union-attr]

    cs2 = _stage_embed_art(db_session, track, blob_b.id, title="Re-embed art")
    apply_changeset(db_session, cs2.id, blob_store=store)
    db_session.commit()
    db_session.refresh(track)

    assert track.art_blob_id == blob_b.id
    # blob_a's refcount hit 0 -> BlobStore.release deletes the row entirely.
    assert db_session.get(Blob, blob_a.id) is None
    refreshed_blob_b = db_session.get(Blob, blob_b.id)
    assert refreshed_blob_b is not None
    assert refreshed_blob_b.refcount == 1

    on_disk = read_track(Path(track.path))
    assert on_disk.has_embedded_art is True


def test_clearing_art_releases_the_blob_and_removes_embedded_picture(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, _JPEG_A, mime="image/jpeg")
    db_session.commit()

    cs1 = _stage_embed_art(db_session, track, blob.id)
    apply_changeset(db_session, cs1.id, blob_store=store)
    db_session.commit()
    db_session.refresh(track)
    assert track.has_embedded_art is True

    cs2 = _stage_embed_art(db_session, track, None, title="Clear art")
    apply_changeset(db_session, cs2.id, blob_store=store)
    db_session.commit()
    db_session.refresh(track)

    assert track.has_embedded_art is False
    assert track.art_blob_id is None
    assert db_session.get(Blob, blob.id) is None
    on_disk = read_track(Path(track.path))
    assert on_disk.has_embedded_art is False


def test_undo_of_embed_art_restores_previous_state(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, _JPEG_A, mime="image/jpeg")
    db_session.commit()

    cs = _stage_embed_art(db_session, track, blob.id)
    apply_changeset(db_session, cs.id, blob_store=store)
    db_session.commit()
    db_session.refresh(track)
    assert track.has_embedded_art is True

    undo_cs = build_undo_changeset(db_session, cs.id)
    db_session.commit()
    undo_result = apply_changeset(db_session, undo_cs.id, blob_store=store)
    db_session.commit()

    assert undo_result.state == "applied"
    db_session.refresh(track)
    assert track.has_embedded_art is False
    assert track.art_blob_id is None
    assert db_session.get(Blob, blob.id) is None

    on_disk = read_track(Path(track.path))
    assert on_disk.has_embedded_art is False


def test_apply_journal_records_art_phase_when_no_tag_fields_changed(
    db_session: Session, tmp_path: Path
) -> None:
    track = _scan_one(db_session, tmp_path)
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, _JPEG_A, mime="image/jpeg")
    db_session.commit()

    cs = _stage_embed_art(db_session, track, blob.id)
    apply_changeset(db_session, cs.id, blob_store=store)
    db_session.commit()

    journal_rows = db_session.query(ApplyJournal).filter(ApplyJournal.change_set_id == cs.id).all()
    assert len(journal_rows) == 1
    assert journal_rows[0].phase == "art"
    assert journal_rows[0].state == "done"
