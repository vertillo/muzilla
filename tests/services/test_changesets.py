from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.changes.blobstore import BlobStore
from muzilla.changes.builder import FieldEdit, build_changeset
from muzilla.db.models import Track, TrackGroup
from muzilla.pipeline.scan import scan_library
from muzilla.services import changesets as changesets_service
from muzilla.services import edit as edit_service
from muzilla.services import grouping as grouping_service

FIXTURES = Path(__file__).parent.parent / "fixtures" / "audio"


def _scan_one(db_session: Session, tmp_path: Path) -> Track:
    library = tmp_path / "library"
    library.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIXTURES / "silence.mp3", library / "a.mp3")
    scan_library(db_session, library)
    db_session.commit()
    return db_session.query(Track).one()


def test_get_changeset_includes_field_diffs(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "New Title"})
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    assert len(detail.changes) == 1
    change = detail.changes[0]
    assert change.diff.kind == "text"
    assert change.diff.new_value == "New Title"


def test_get_changeset_missing_returns_none(db_session: Session) -> None:
    assert changesets_service.get_changeset(db_session, 999) is None


def test_get_changeset_binary_diff_includes_blob_summary(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    store = BlobStore(tmp_path / "blobs")
    blob = store.put(db_session, b"jpeg bytes" * 50, mime="image/jpeg", width=500, height=500)
    db_session.commit()

    cs = build_changeset(
        db_session,
        title="Embed art",
        source="enrichment",
        edits={track.id: [FieldEdit(field="art", new_value=None, op="embed_art", new_blob_id=blob.id)]},
    )
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    change = detail.changes[0]
    assert change.diff.kind == "binary"
    assert change.diff.binary is not None
    assert change.diff.binary.old_summary is None
    assert change.diff.binary.new_summary == "500x500 image/jpeg 0.5KB"


def test_apply_decisions_persists_accept_and_manual_edit(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Draft Title"})
    db_session.commit()
    change_id = cs.changes[0].id

    detail = changesets_service.apply_decisions(
        db_session,
        cs.id,
        [changesets_service.ChangeDecision(change_id=change_id, decision="accepted", new_value="Overridden Title")],
    )
    assert detail.changes[0].decision == "accepted"
    assert detail.changes[0].new_value == "Overridden Title"
    assert detail.changes[0].is_manual is True


def test_apply_decisions_on_non_draft_raises(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "X"})
    db_session.commit()
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    with pytest.raises(ValueError, match="not draft"):
        changesets_service.apply_decisions(
            db_session, cs.id, [changesets_service.ChangeDecision(change_id=cs.changes[0].id, decision="rejected")]
        )


def test_list_changesets_pagination(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    for i in range(3):
        edit_service.edit_track(db_session, track_id=track.id, field_values={"title": f"T{i}"})
    db_session.commit()

    page = changesets_service.list_changesets(db_session, limit=2)
    assert page.total == 3
    assert len(page.items) == 2
    assert page.next_cursor is not None

    page2 = changesets_service.list_changesets(db_session, limit=2, cursor=page.next_cursor)
    assert len(page2.items) == 1


def test_full_apply_undo_roundtrip_via_service(db_session: Session, tmp_path: Path) -> None:
    track = _scan_one(db_session, tmp_path)
    original_title = track.title
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    result = changesets_service.apply_now(db_session, cs.id)
    assert result.state == "applied"

    undo_detail = changesets_service.undo_now(db_session, cs.id)
    assert undo_detail.source == f"undo_of:{cs.id}"

    changesets_service.apply_now(db_session, undo_detail.id)
    db_session.refresh(track)
    assert track.title == original_title


def test_apply_enqueues_job_and_returns_its_id(db_session: Session, tmp_path: Path) -> None:
    from muzilla.jobs import queue

    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()

    job_id = changesets_service.apply(db_session, cs.id)
    job = queue.get_job(db_session, job_id)
    assert job is not None
    assert job.type == "apply_changeset"
    assert job.payload == {"change_set_id": cs.id}


def test_undo_enqueues_job_and_returns_its_id(db_session: Session, tmp_path: Path) -> None:
    from muzilla.jobs import queue

    track = _scan_one(db_session, tmp_path)
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"title": "Changed"})
    for c in cs.changes:
        c.decision = "accepted"
    db_session.commit()
    changesets_service.apply_now(db_session, cs.id)

    job_id = changesets_service.undo(db_session, cs.id)
    job = queue.get_job(db_session, job_id)
    assert job is not None
    assert job.type == "undo_changeset"
    assert job.payload == {"change_set_id": cs.id}


def test_recover_apply_journal_delegates_to_changes_applier(
    db_session: Session, tmp_path: Path
) -> None:
    # No stuck journal rows in a fresh DB -- confirms the service
    # wrapper is wired correctly rather than duplicating the logic.
    report = changesets_service.recover_apply_journal(db_session)
    assert report.reverted == 0
    assert report.confirmed_done == 0


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1000, mtime_ns=1, **kwargs)  # type: ignore[arg-type]
    session.add(t)
    session.flush()
    return t


def test_get_changeset_labels_singleton_track_by_artist_and_title(
    db_session: Session, tmp_path: Path
) -> None:
    # docs/product-spec.md: an ungrouped track (no group_id) is a
    # singleton -- labeled "artist - title", never a bare track number.
    track = _scan_one(db_session, tmp_path)
    assert track.group_id is None
    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"year": 2000})
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    assert len(detail.entities) == 1
    entity = detail.entities[0]
    assert entity.entity_type == "track"
    assert entity.entity_id == track.id
    assert entity.label == f"{track.artist} – {track.title}"  # noqa: RUF001
    assert entity.sort_key == track.track_no


def test_get_changeset_labels_album_mode_track_by_position(db_session: Session) -> None:
    # docs/product-spec.md: a track whose group has more than one
    # track is in "album mode" -- labeled "N. title", sorted by track_no.
    group = TrackGroup(key="k1", album="Album", album_artist="Artist", track_count=2)
    db_session.add(group)
    db_session.flush()
    t1 = _make_track(db_session, path="/a1", title="First", track_no=1, group_id=group.id)
    t2 = _make_track(db_session, path="/a2", title="Second", track_no=2, group_id=group.id)
    db_session.commit()

    edits = {
        t1.id: [FieldEdit(field="year", new_value=2001, is_manual=True)],
        t2.id: [FieldEdit(field="year", new_value=2001, is_manual=True)],
    }
    cs = build_changeset(
        db_session,
        title="bulk edit",
        source="manual_edit",
        edits=edits,
        entity_type="track",
        scope_type="track",
    )
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    assert [e.label for e in detail.entities] == ["1. First", "2. Second"]
    assert [e.sort_key for e in detail.entities] == [1, 2]


def test_get_changeset_labels_group_entity_by_artist_and_album(db_session: Session) -> None:
    group = TrackGroup(key="k2", album="Ágætis byrjun", album_artist="Sigur Rós")
    db_session.add(group)
    db_session.flush()
    db_session.commit()

    cs = grouping_service.pin_group(db_session, group_id=group.id)
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    assert len(detail.entities) == 1
    entity = detail.entities[0]
    assert entity.entity_type == "group"
    assert entity.entity_id == group.id
    assert entity.label == "Sigur Rós – Ágætis byrjun"  # noqa: RUF001
    assert entity.sort_key is None


def test_get_changeset_entity_label_falls_back_when_data_is_missing(db_session: Session) -> None:
    # No artist, no title, no group -- must not crash, and must still
    # produce a usable label rather than an empty string.
    track = _make_track(db_session, path="/untagged")
    db_session.commit()

    cs = edit_service.edit_track(db_session, track_id=track.id, field_values={"year": 1999})
    db_session.commit()

    detail = changesets_service.get_changeset(db_session, cs.id)
    assert detail is not None
    entity = detail.entities[0]
    assert entity.label == f"Unknown artist – {track.filename}"  # noqa: RUF001


def _stage_bulk_edit(session: Session, tracks: list[Track]) -> object:
    edits = {
        t.id: [FieldEdit(field="year", new_value=2001, is_manual=True)] for t in tracks
    }
    return build_changeset(
        session,
        title="bulk edit",
        source="manual_edit",
        edits=edits,
        entity_type="track",
        scope_type="track",
    )


def _count_entity_lookup_queries(session: Session, change_set_id: int) -> int:
    from sqlalchemy import event

    query_count = 0

    def _count(*_args: object, **_kwargs: object) -> None:
        nonlocal query_count
        query_count += 1

    event.listen(session.bind, "before_cursor_execute", _count)
    try:
        changesets_service.get_changeset(session, change_set_id)
    finally:
        event.remove(session.bind, "before_cursor_execute", _count)
    return query_count


def test_get_changeset_entities_batches_lookups_not_one_query_per_entity(
    db_session: Session,
) -> None:
    # CLAUDE.md / §11g: unbatched IN() sites are a recurring defect here
    # (docs/product-spec.md gotcha 23: an N+1 spotted by reading the code and
    # the actual bottleneck under load are not guaranteed to be the same
    # line -- confirm by measurement, not by reading _build_entities and
    # trusting its own docstring). Compare query counts at two sizes: if
    # entity lookups were one-query-per-entity, going from 2 to 8 tracks
    # would roughly quadruple the count; batched, it should barely move.
    group = TrackGroup(key="k3", album="Album", album_artist="Artist", track_count=8)
    db_session.add(group)
    db_session.flush()

    small_tracks = [
        _make_track(db_session, path=f"/small{i}", title=f"T{i}", track_no=i, group_id=group.id)
        for i in range(2)
    ]
    db_session.commit()
    small_cs = _stage_bulk_edit(db_session, small_tracks)
    db_session.commit()
    small_count = _count_entity_lookup_queries(db_session, small_cs.id)

    large_tracks = small_tracks + [
        _make_track(db_session, path=f"/large{i}", title=f"T{i}", track_no=i, group_id=group.id)
        for i in range(2, 8)
    ]
    db_session.commit()
    large_cs = _stage_bulk_edit(db_session, large_tracks)
    db_session.commit()
    large_count = _count_entity_lookup_queries(db_session, large_cs.id)

    # Batched: query count is flat regardless of entity count (allow a
    # small constant-factor margin for SQLAlchemy's own bookkeeping
    # queries, e.g. transaction begin). One-query-per-entity would show
    # large_count >= small_count + 4 (the four extra tracks); batched
    # implementations should differ by at most 1-2.
    assert large_count <= small_count + 2, (
        f"entity lookups appear unbatched: {small_count} queries for 2 tracks, "
        f"{large_count} for 8"
    )
