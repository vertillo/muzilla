from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.models import Track, TrackGroup
from muzilla.paths.errors import TemplateError
from muzilla.services import paths as paths_service


def _make_track(session: Session, *, path: str, ext: str = ".mp3", **kwargs: object) -> Track:
    t = Track(
        path=path, filename=path.rsplit("/", 1)[-1], ext=ext, size_bytes=1, mtime_ns=1, **kwargs
    )
    session.add(t)
    session.flush()
    return t


_group_counter = 0


def _make_group(session: Session, **kwargs: object) -> TrackGroup:
    global _group_counter
    _group_counter += 1
    key = kwargs.pop("key", None) or f"k-{_group_counter}"
    g = TrackGroup(key=key, **kwargs)
    session.add(g)
    session.flush()
    return g


def _default_config(**overrides: object) -> PathsConfig:
    base = PathsConfig(
        album="$albumartist - $album - $track $title",
        singleton="$artist - $title",
        default="$artist - $title",
    )
    return base.model_copy(update=overrides)


# --- render_path_for_track ----------------------------------------------------


def test_render_path_for_track_singleton(db_session: Session) -> None:
    t = _make_track(db_session, path="/s1.mp3", title="Solo", artist="Artist")
    g = _make_group(db_session, kind="singleton")
    t.group_id = g.id
    db_session.commit()

    row = paths_service.render_path_for_track(db_session, t.id, config=_default_config())
    assert row.new_path == "Artist - Solo.mp3"
    assert row.old_path == "/s1.mp3"
    assert row.errors == ()


def test_render_path_for_track_album(db_session: Session) -> None:
    t = _make_track(
        db_session,
        path="/a1.mp3",
        title="Track One",
        track_no=1,
        album="Album",
        album_artist="Band",
    )
    g = _make_group(db_session, kind="album", album="Album", album_artist="Band")
    t.group_id = g.id
    db_session.commit()

    row = paths_service.render_path_for_track(db_session, t.id, config=_default_config())
    assert row.new_path == "Band - Album - 1 Track One.mp3"


def test_render_path_for_track_no_group_uses_default(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y")
    db_session.commit()

    row = paths_service.render_path_for_track(db_session, t.id, config=_default_config())
    assert row.new_path == "Y - X.mp3"


def test_render_path_for_track_missing_track_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        paths_service.render_path_for_track(db_session, 99999, config=_default_config())


def test_render_path_for_track_with_template_override(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y")
    db_session.commit()

    row = paths_service.render_path_for_track(
        db_session, t.id, config=_default_config(), template_override="%upper{$artist}"
    )
    assert row.new_path == "Y.mp3"


def test_render_path_for_track_query_override(db_session: Session) -> None:
    """A directory separator is valid only when directory creation is enabled."""
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y", genre=["Classical"])
    db_session.commit()

    config = _default_config(
        overrides={"genre:Classical": "Classical/$artist"}, create_directories=True
    )
    row = paths_service.render_path_for_track(db_session, t.id, config=config)
    assert row.errors == ()
    assert row.new_path == "Classical/Y.mp3"


# --- preview_rename -------------------------------------------------------------


def test_preview_rename_by_group_id(db_session: Session) -> None:
    g = _make_group(db_session, kind="album", album="Al", album_artist="Band")
    t1 = _make_track(
        db_session, path="/a1.mp3", title="T1", track_no=1, album="Al", album_artist="Band"
    )
    t2 = _make_track(
        db_session, path="/a2.mp3", title="T2", track_no=2, album="Al", album_artist="Band"
    )
    t1.group_id = g.id
    t2.group_id = g.id
    db_session.commit()

    rows = paths_service.preview_rename(db_session, group_id=g.id, config=_default_config())
    assert len(rows) == 2
    assert {r.new_path for r in rows} == {"Band - Al - 1 T1.mp3", "Band - Al - 2 T2.mp3"}
    assert all(not r.is_collision for r in rows)


def test_preview_rename_by_track_ids(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="A", artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="B", artist="X")
    db_session.commit()

    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert {r.track_id for r in rows} == {t1.id, t2.id}


def test_preview_rename_flags_batch_collision(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Same", artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="Same", artist="X")
    db_session.commit()

    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)


def test_preview_rename_flags_collision_with_existing_library_file(db_session: Session) -> None:
    # This untouched track's CURRENT on-disk path is already exactly
    # what the mover's template would render to -- a real collision,
    # since the untouched track isn't being renamed and would keep
    # occupying that path.
    _make_track(db_session, path="Y - X.mp3", title="Untouched", artist="Other")
    mover = _make_track(db_session, path="/mover.mp3", title="X", artist="Y")
    db_session.commit()

    rows = paths_service.preview_rename(db_session, track_ids=[mover.id], config=_default_config())
    assert len(rows) == 1
    assert rows[0].is_collision is True


def test_preview_rename_no_tracks_returns_empty(db_session: Session) -> None:
    rows = paths_service.preview_rename(db_session, track_ids=[], config=_default_config())
    assert rows == []


def test_preview_rename_missing_group_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        paths_service.preview_rename(db_session, group_id=99999, config=_default_config())


# --- no automatic disambiguation — %aunique/%sunique removed ----------------


def test_aunique_template_is_rejected(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="T", artist="A")
    db_session.commit()
    with pytest.raises(TemplateError, match="unknown function"):
        paths_service.preview_rename(
            db_session,
            track_ids=[t.id],
            config=_default_config(),
            template_override="$artist - $title%aunique{}",
        )


def test_sunique_template_is_rejected(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="T", artist="A")
    db_session.commit()
    with pytest.raises(TemplateError, match="unknown function"):
        paths_service.preview_rename(
            db_session,
            track_ids=[t.id],
            config=_default_config(),
            template_override="$artist - $title%sunique{}",
        )


def test_collision_case_insensitive(db_session: Session) -> None:
    # Filesystems like Windows/macOS are case-insensitive — "Foo.mp3" and "foo.mp3" collide.
    t1 = _make_track(db_session, path="/a.mp3", title="Foo", artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="foo", artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)


def test_collision_unicode_normalization(db_session: Session) -> None:
    import unicodedata

    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd
    t1 = _make_track(db_session, path="/a.mp3", title=nfc, artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title=nfd, artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)


def test_collision_long_name_truncation(db_session: Session) -> None:
    # Distinct long titles that truncate to different 255-byte filenames must NOT collide.
    t1 = _make_track(db_session, path="/a.mp3", title="a" * 300, artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="b" * 300, artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert len(rows) == 2
    assert all(not r.is_collision for r in rows)
    assert rows[0].new_path != rows[1].new_path
    assert len(rows[0].new_path.encode("utf-8")) <= 255
    assert len(rows[1].new_path.encode("utf-8")) <= 255
    # Identical long titles that truncate to the same 255-byte filename must collide (no silent suffix).
    t3 = _make_track(db_session, path="/c.mp3", title="a" * 300, artist="Y")
    t4 = _make_track(db_session, path="/d.mp3", title="a" * 300, artist="Y")
    db_session.commit()
    rows2 = paths_service.preview_rename(
        db_session, track_ids=[t3.id, t4.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows2)
    assert rows2[0].collision_path == rows2[1].collision_path
    assert set(rows2[0].conflicting_track_ids) == {t4.id}
    assert set(rows2[1].conflicting_track_ids) == {t3.id}


def test_collision_multi_artist_join(db_session: Session) -> None:
    # MULTI_TEXT artists joined with ", " — same joined string collides
    t1 = _make_track(db_session, path="/a.mp3", title="T", artist="A", artists=["A", "B"])
    t2 = _make_track(db_session, path="/b.mp3", title="T", artist="A", artists=["A", "B"])
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)


def test_collision_multidisc_distinct_paths_not_colliding(db_session: Session) -> None:
    # Distinct disc_no/track_no must not collide when template includes disc
    # Tracks are in album groups so the album template is used (singleton
    # template would drop $disc and falsely collide).
    config = _default_config(album="$albumartist - $album - $disc $track $title")
    g1 = _make_group(db_session, kind="album", album="Al", album_artist="Band")
    g2 = _make_group(db_session, kind="album", album="Al", album_artist="Band")
    t1 = _make_track(
        db_session, path="/a.mp3", title="T", album="Al", album_artist="Band", disc_no=1, track_no=1
    )
    t2 = _make_track(
        db_session, path="/b.mp3", title="T", album="Al", album_artist="Band", disc_no=2, track_no=1
    )
    t1.group_id = g1.id
    t2.group_id = g2.id
    db_session.commit()
    rows = paths_service.preview_rename(db_session, track_ids=[t1.id, t2.id], config=config)
    assert all(not r.is_collision for r in rows)
    # Same disc/track with same metadata does collide
    g3 = _make_group(db_session, kind="album", album="Al", album_artist="Band")
    g4 = _make_group(db_session, kind="album", album="Al", album_artist="Band")
    t3 = _make_track(
        db_session, path="/c.mp3", title="U", album="Al", album_artist="Band", disc_no=1, track_no=1
    )
    t4 = _make_track(
        db_session, path="/d.mp3", title="U", album="Al", album_artist="Band", disc_no=1, track_no=1
    )
    t3.group_id = g3.id
    t4.group_id = g4.id
    db_session.commit()
    rows2 = paths_service.preview_rename(db_session, track_ids=[t3.id, t4.id], config=config)
    assert all(r.is_collision for r in rows2)


def test_collision_reports_all_conflicting_ids_and_path(db_session: Session) -> None:
    # Three-way collision must list every other participant and the shared destination.
    t1 = _make_track(db_session, path="/a1.mp3", title="Same", artist="X")
    t2 = _make_track(db_session, path="/a2.mp3", title="Same", artist="X")
    t3 = _make_track(db_session, path="/a3.mp3", title="Same", artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id, t3.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)
    by_id = {r.track_id: r for r in rows}
    assert set(by_id[t1.id].conflicting_track_ids) == {t2.id, t3.id}
    assert set(by_id[t2.id].conflicting_track_ids) == {t1.id, t3.id}
    assert set(by_id[t3.id].conflicting_track_ids) == {t1.id, t2.id}
    assert (
        by_id[t1.id].collision_path
        == by_id[t2.id].collision_path
        == by_id[t3.id].collision_path
        == "X - Same.mp3"
    )


def test_collision_existing_multiple_normalized_paths(db_session: Session) -> None:
    # Two existing library files whose paths differ only by case/Unicode must both be reported.
    import unicodedata

    nfc = unicodedata.normalize("NFC", "café")
    nfd = unicodedata.normalize("NFD", "café")
    assert nfc != nfd
    # Two untouched tracks already occupy normalized-same destinations (different spellings, same norm)
    _make_track(db_session, path=f"X - {nfc}.mp3", title="Other1", artist="Z")
    _make_track(db_session, path=f"X - {nfd}.mp3", title="Other2", artist="Z")
    mover = _make_track(db_session, path="/mover.mp3", title="café", artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(db_session, track_ids=[mover.id], config=_default_config())
    assert rows[0].is_collision is True
    # mover collides with both existing tracks (existing_by_norm list must retain all)
    assert len(rows[0].conflicting_track_ids) == 2


def test_collision_sanitized_values_collide(db_session: Session) -> None:
    # Two distinct titles that sanitize to the same final destination must collide (invalid-name handling).
    t1 = _make_track(db_session, path="/a.mp3", title="AC:DC", artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="AC?DC", artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)
    assert rows[0].new_path == rows[1].new_path == "X - AC_DC.mp3"
    assert set(rows[0].conflicting_track_ids) == {t2.id}


def test_no_clobber_filesystem_destination_blocks_apply(
    tmp_path: Path, db_session: Session
) -> None:
    # Destination already exists on filesystem after preview must block the whole bundle (no overwrite).
    import shutil

    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.db.models import Track
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import put_revision, transition_bundle
    from muzilla.services.reviews import OperationDraft

    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    # Create a real audio file
    src_file = Path(__file__).parent.parent / "fixtures/audio/silence.mp3"
    f1 = lib / "orig1.mp3"
    shutil.copy2(src_file, f1)
    # Create Track row from that file
    from datetime import UTC, datetime

    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.tags.reader import read_track

    def _track_from_file(p: Path) -> Track:
        st = p.stat()
        meta = read_track(p)
        h = compute_tag_hash(meta)
        now = datetime.now(UTC)
        t = Track(
            path=str(p),
            filename=p.name,
            ext=p.suffix,
            size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            title=meta.title or "t",
            artist=meta.artist or "a",
            tag_hash=h,
            first_seen_at=now,
            last_scanned_at=now,
        )
        db_session.add(t)
        db_session.flush()
        return t

    t1 = _track_from_file(f1)
    db_session.commit()
    # Destination that will collide on filesystem
    dest = "X - New.mp3"
    existing_dest = lib / dest
    existing_dest.write_bytes(b"existing")
    # Build a bundle that moves t1 to dest (which already exists on FS)
    logical_key = f"track:{t1.id}:no-clobber"
    write = put_revision(
        db_session,
        logical_key=logical_key,
        title="no-clobber test",
        scope_type="track",
        scope_id=t1.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": t1.id,
                    "path": t1.path,
                    "size_bytes": t1.size_bytes,
                    "mtime_ns": t1.mtime_ns,
                    "tag_hash": t1.tag_hash,
                    "filename": t1.filename,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=t1.id,
                current_value=t1.title,
                proposed_value="New",
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=t1.id,
                current_value=t1.path,
                proposed_value=dest,
                provenance={"section": "path"},
                validation={"errors": [], "collision": False},
            ),
        ),
    )
    for op in db_session.scalars(
        __import__("sqlalchemy", fromlist=["select"])
        .select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation)
        .where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == write.revision_id
        )
    ):
        op.decision = "accepted"
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()
    from muzilla.pipeline.reviews import start_apply_run

    run = start_apply_run(db_session, write.bundle_id, idempotency_key="no-clobber-1")
    db_session.commit()
    result = apply_review_run(
        db_session, run.id, library_root=lib, blob_store=BlobStore(tmp_path / "blobs")
    )
    assert result.state == "failed"
    # No overwrite: existing dest file still has original content
    assert existing_dest.read_bytes() == b"existing"
    # Whole bundle failed atomically, not partially applied
    # orig file still exists at its original path
    assert f1.exists()
    assert db_session.get(Track, t1.id) is not None
    # Recovery not required for validation failure
    assert result.recovery_required is False


# --- extension preservation ------------------------------------------------------


def test_rendered_path_preserves_source_file_extension(db_session: Session) -> None:
    """Rendered rename paths retain the source file's extension."""
    t = _make_track(db_session, path="/old.flac", title="X", artist="Y", ext=".flac")
    db_session.commit()

    row = paths_service.render_path_for_track(db_session, t.id, config=_default_config())
    assert row.new_path == "Y - X.flac"
    assert row.errors == ()


def test_rendered_path_with_error_is_not_given_a_spurious_extension(db_session: Session) -> None:
    """An errored render's `new_path` is the raw error-path artifact
    (e.g. a rendered `/` in flat mode) -- appending an extension to
    that would be actively misleading, since it was never a real
    candidate filename."""
    t = _make_track(db_session, path="/old.mp3", title="X", artist="Y")
    db_session.commit()

    row = paths_service.render_path_for_track(
        db_session, t.id, config=_default_config(), template_override="literal/slash/$artist"
    )
    assert row.errors != ()
    assert not row.new_path.endswith(".mp3")


# --- performance ---------------------------------------------------------------


def test_preview_rename_does_not_issue_one_query_per_track_for_group_kind(
    db_session: Session,
) -> None:
    """Rename preview loads group kinds in a bounded batch query."""
    from sqlalchemy import event

    # Use one distinct group per track so an identity-map cache cannot hide
    # a per-track query.
    tracks = []
    for i in range(30):
        g = _make_group(db_session, kind="album", album=f"Al{i}", album_artist=f"Band{i}")
        t = _make_track(
            db_session,
            path=f"/t{i}.mp3",
            title=f"T{i}",
            track_no=1,
            album=f"Al{i}",
            album_artist=f"Band{i}",
        )
        t.group_id = g.id
        tracks.append(t)
    db_session.commit()

    statement_count = 0

    def _count(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(db_session.get_bind(), "before_cursor_execute", _count)
    try:
        rows = paths_service.preview_rename(
            db_session, track_ids=[t.id for t in tracks], config=_default_config()
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _count)

    assert len(rows) == 30
    # The query count should stay bounded independently of the track count.
    assert statement_count < 20, (
        f"expected a small, batch-scoped query count, got {statement_count} "
        f"statements for 30 tracks across 30 distinct groups"
    )


def test_preview_rename_collision_check_does_not_load_full_track_rows(
    db_session: Session,
) -> None:
    """Collision detection reads only ``Track.id`` and ``Track.path``.

    Excluding the renamed batch in Python keeps the query below SQLite's
    variable limit, so the assertion identifies the column-scoped query.
    """
    from sqlalchemy import event

    other = _make_track(db_session, path="/other.mp3", title="Other", artist="X", genre=["A", "B"])
    mover = _make_track(db_session, path="/mover.mp3", title="Y", artist="Z")
    db_session.commit()

    statements: list[str] = []

    def _capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", _capture)
    try:
        rows = paths_service.preview_rename(
            db_session, track_ids=[mover.id], config=_default_config()
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _capture)

    assert len(rows) == 1
    # Match the exact two-column shape; a full ORM-row query has the same
    # prefix but selects additional columns.
    normalized = [" ".join(s.split()).lower() for s in statements]
    collision_queries = [s for s in normalized if s == "select tracks.id, tracks.path from tracks"]
    assert collision_queries, (
        f"expected the column-scoped id/path collision query, got: {normalized}"
    )
    _ = other  # exists only to give the library-scan something to (not) load in full


def _bulk_insert_tracks(session: Session, count: int, *, prefix: str = "perf") -> list[int]:
    """Fast Core-level bulk insert (~0.5s for 35k rows vs. ~14s through
    the ORM-object-per-row `_make_track` helper) for tests that only
    need track ids to exist, not any grouping/rendering behavior."""
    from sqlalchemy import insert, select

    rows = [
        {
            "path": f"/{prefix}/{i}.mp3",
            "filename": f"{i}.mp3",
            "ext": ".mp3",
            "size_bytes": 1,
            "mtime_ns": 1,
        }
        for i in range(count)
    ]
    session.execute(insert(Track), rows)
    session.commit()
    return [
        row[0] for row in session.execute(select(Track.id).where(Track.path.like(f"/{prefix}/%")))
    ]


def test_resolve_track_set_does_not_crash_past_sqlite_variable_limit(
    db_session: Session,
) -> None:
    """Resolving a large track selection stays within SQLite's bind limit."""
    ids = _bulk_insert_tracks(db_session, 35_000)

    rows = paths_service.preview_rename(db_session, track_ids=ids, config=_default_config())
    assert len(rows) == 35_000


def test_collision_check_does_not_crash_past_sqlite_variable_limit(
    db_session: Session,
) -> None:
    """Collision filtering remains safe when the renamed batch is large."""
    ids = _bulk_insert_tracks(db_session, 35_000)

    rows = paths_service.preview_rename(db_session, track_ids=ids, config=_default_config())
    assert len(rows) == 35_000


def test_move_no_clobber_does_not_overwrite(tmp_path: Path) -> None:
    from muzilla.changes.writer import _move_no_clobber

    src = tmp_path / "src.mp3"
    src.write_bytes(b"original")
    dst = tmp_path / "dst.mp3"
    dst.write_bytes(b"existing")
    # same_file=False with existing dest must raise, not overwrite
    try:
        _move_no_clobber(src, dst, same_file=False)
    except OSError as exc:
        assert "exists" in str(exc).lower() or exc.errno in (17, 17)
    else:
        raise AssertionError("expected OSError for existing destination")
    assert src.read_bytes() == b"original"
    assert dst.read_bytes() == b"existing"


def test_apply_race_destination_appearing_after_preflight_is_not_overwritten(
    tmp_path: Path, db_session: Session
) -> None:
    """Destination created after preflight must still be blocked via atomic no-replace."""
    import shutil
    from datetime import UTC, datetime
    from unittest.mock import patch

    from muzilla.changes.blobstore import BlobStore
    from muzilla.changes.bundle_applier import apply_review_run
    from muzilla.changes.writer import _move_no_clobber as real_move
    from muzilla.db.models import Track
    from muzilla.domain.metadata import tag_hash as compute_tag_hash
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import put_revision, start_apply_run
    from muzilla.services.reviews import OperationDraft
    from muzilla.tags.reader import read_track

    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    src_file = Path(__file__).parent.parent / "fixtures/audio/silence.mp3"
    f1 = lib / "orig1.mp3"
    shutil.copy2(src_file, f1)

    def _track_from_file(p: Path) -> Track:
        st = p.stat()
        meta = read_track(p)
        h = compute_tag_hash(meta)
        now = datetime.now(UTC)
        t = Track(
            path=str(p),
            filename=p.name,
            ext=p.suffix,
            size_bytes=st.st_size,
            mtime_ns=st.st_mtime_ns,
            title=meta.title or "t",
            artist=meta.artist or "a",
            tag_hash=h,
            first_seen_at=now,
            last_scanned_at=now,
        )
        db_session.add(t)
        db_session.flush()
        return t

    t1 = _track_from_file(f1)
    db_session.commit()
    dest = "X - New.mp3"
    dest_path = lib / dest
    # No file at preflight time; race creates it just before atomic move
    logical_key = f"track:{t1.id}:race-no-clobber"
    write = put_revision(
        db_session,
        logical_key=logical_key,
        title="race test",
        scope_type="track",
        scope_id=t1.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": t1.id,
                    "path": t1.path,
                    "size_bytes": t1.size_bytes,
                    "mtime_ns": t1.mtime_ns,
                    "tag_hash": t1.tag_hash,
                    "filename": t1.filename,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=t1.id,
                current_value=t1.title,
                proposed_value="New",
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=t1.id,
                current_value=t1.path,
                proposed_value=dest,
                provenance={"section": "path"},
                validation={"errors": [], "collision": False},
            ),
        ),
    )
    for op in db_session.scalars(
        __import__("sqlalchemy", fromlist=["select"])
        .select(__import__("muzilla.db.models", fromlist=["Operation"]).Operation)
        .where(
            __import__("muzilla.db.models", fromlist=["Operation"]).Operation.proposal_revision_id
            == write.revision_id
        )
    ):
        op.decision = "accepted"
    from muzilla.pipeline.reviews import transition_bundle

    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()
    run = start_apply_run(db_session, write.bundle_id, idempotency_key="race-1")
    db_session.commit()

    def race_move(source: Path, destination: Path, *, same_file: bool) -> None:
        # Simulate concurrent writer creating destination after preflight
        if not destination.exists():
            destination.write_bytes(b"race-winner")
        return real_move(source, destination, same_file=same_file)

    with patch("muzilla.changes.writer._move_no_clobber", side_effect=race_move):
        result = apply_review_run(
            db_session, run.id, library_root=lib, blob_store=BlobStore(tmp_path / "blobs")
        )
    assert result.state == "failed"
    # No overwrite: race winner and original source still present, no clobber
    assert dest_path.read_bytes() == b"race-winner"
    assert f1.exists()
    assert f1.read_bytes() != b"race-winner"


def test_edit_recomputes_move_and_resolves_collision(db_session: Session) -> None:
    """Editing metadata to resolve collision must recalc move preview (PATH-COLLISION-001)."""
    from muzilla.db.models import ProposalRevision
    from muzilla.domain.reviews import BundleState
    from muzilla.pipeline.reviews import (
        edit_operation,
        get_review_bundle,
        put_revision,
        transition_bundle,
    )
    from muzilla.services.reviews import OperationDraft

    # Create two tracks whose current preview collides (same artist/title)
    t1 = _make_track(db_session, path="/a1.mp3", title="Same", artist="X")
    t2 = _make_track(db_session, path="/a2.mp3", title="Same", artist="X")
    db_session.commit()
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    assert all(r.is_collision for r in rows)
    # Build a group-like bundle with colliding moves via manual put_revision
    logical_key = f"track:{t1.id}:edit-recalc"
    # Use t1's colliding preview as initial move
    colliding_dest = rows[0].new_path

    write = put_revision(
        db_session,
        logical_key=logical_key,
        title="edit recalc",
        scope_type="track",
        scope_id=t1.id,
        source_snapshot={
            "items": [
                {
                    "source_type": "track",
                    "source_id": t1.id,
                    "path": t1.path,
                    "size_bytes": t1.size_bytes,
                    "mtime_ns": t1.mtime_ns,
                    "tag_hash": t1.tag_hash or "",
                    "filename": t1.filename,
                }
            ]
        },
        operations=(
            OperationDraft(
                kind="set_tag",
                field="title",
                target_type="track",
                target_id=t1.id,
                current_value=t1.title,
                proposed_value="Same",
            ),
            OperationDraft(
                kind="move_file",
                field="path",
                target_type="track",
                target_id=t1.id,
                current_value=t1.path,
                proposed_value=colliding_dest,
                provenance={"section": "path"},
                validation={
                    "errors": [],
                    "collision": True,
                    "conflicting_track_ids": [t2.id],
                    "collision_path": colliding_dest,
                },
            ),
        ),
    )
    transition_bundle(db_session, write.bundle_id, BundleState.READY)
    db_session.commit()
    detail = get_review_bundle(db_session, write.bundle_id)
    assert detail is not None
    # Find the SET_TAG operation to edit
    rev = db_session.scalar(
        __import__("sqlalchemy", fromlist=["select"])
        .select(ProposalRevision)
        .where(
            ProposalRevision.review_bundle_id == write.bundle_id,
            ProposalRevision.is_current.is_(True),
        )
    )
    op = next(o for o in rev.operations if o.kind == "set_tag" and o.field == "title")
    # Edit title to a unique value → move should be recomputed and collision cleared
    edited = edit_operation(
        db_session,
        bundle_id=write.bundle_id,
        operation_id=op.id,
        revision_id=rev.id,
        kind="set_tag",
        value="Unique Title 123",
    )
    move_ops = [o for o in edited.current_revision.operations if o.kind == "move_file"]
    # After recompute, move destination must reflect new title and not be colliding
    assert move_ops, "move should still exist after edit"
    for m in move_ops:
        assert not m.validation.get("collision")
        conflicting = m.validation.get("conflicting_track_ids")
        assert conflicting in ([], None) or (
            isinstance(conflicting, list) and len(conflicting) == 0
        )
        assert "Unique Title 123" in str(m.proposed_value)


def test_move_no_clobber_samefile_distinct_hardlink_fails_closed(tmp_path: Path) -> None:
    """P0: distinct hard-link aliases (same inode, different paths) must fail closed."""
    from muzilla.changes.writer import _move_no_clobber

    src = tmp_path / "src_hard.mp3"
    src.write_bytes(b"original")
    dst = tmp_path / "dst_hard.mp3"
    # Create hard-link alias: different lexical path, same inode.
    try:
        dst.hardlink_to(src)  # Python 3.10+; fallback to os.link
    except AttributeError:
        import os

        os.link(src, dst)
    assert src.stat().st_ino == dst.stat().st_ino
    assert str(src) != str(dst)
    # same_file=True indicates caller detected same inode; distinct alias must not be overwritten.
    try:
        _move_no_clobber(src, dst, same_file=True)
    except OSError as exc:
        # Fail closed with EEXIST semantics, no overwrite.
        assert exc.errno == 17 or "exists" in str(exc).lower()
    else:
        raise AssertionError("expected OSError for distinct hard-link alias")
    # No overwrite: both names still point to original content (same inode).
    assert src.read_bytes() == b"original"
    assert dst.read_bytes() == b"original"
    assert src.stat().st_ino == dst.stat().st_ino


def test_move_no_clobber_exact_path_is_noop(tmp_path: Path) -> None:
    """P0: exact same path must be a no-op and not perform a filesystem mutation."""
    from muzilla.changes.writer import _move_no_clobber

    src = tmp_path / "exact.mp3"
    src.write_bytes(b"data")
    mtime_before = src.stat().st_mtime_ns
    # same lexical path, same_file=True must be a no-op (no replace, no error).
    _move_no_clobber(src, src, same_file=True)
    assert src.exists()
    assert src.read_bytes() == b"data"
    # No spurious mutation: file still present and content unchanged.
    # mtime may be preserved (no replace); at minimum content is intact.
    assert src.stat().st_mtime_ns == mtime_before


def test_preview_rename_is_pure_no_filesystem_io(db_session: Session, tmp_path: Path) -> None:
    """P1: preview must derive relative paths lexically without filesystem I/O."""
    from unittest.mock import patch

    from muzilla.pipeline import paths as paths_service

    fake_root = tmp_path / "nonexistent_library_root_xyz"
    # Ensure fake_root does NOT exist on filesystem — lexical handling must still work.
    assert not fake_root.exists()
    # Create two tracks whose absolute paths lie lexically under fake_root, but
    # those files are not created on the real filesystem (preview must not touch FS).
    t1 = _make_track(db_session, path=str(fake_root / "a.mp3"), title="Same", artist="X")
    t2 = _make_track(db_session, path=str(fake_root / "b.mp3"), title="Same", artist="X")
    db_session.commit()
    # Patch Path.resolve / exists to prove preview performs no I/O.
    with (
        patch.object(
            Path, "resolve", side_effect=AssertionError("preview must not call Path.resolve")
        ),
        patch.object(
            Path, "exists", side_effect=AssertionError("preview must not call Path.exists")
        ),
    ):
        rows = paths_service.preview_rename(
            db_session, track_ids=[t1.id, t2.id], config=_default_config(), library_root=fake_root
        )
    # Lexical collision still detected (both render to "X - Same.mp3").
    assert len(rows) == 2
    assert all(r.is_collision for r in rows)
    # No filesystem side-effect: fake_root still does not exist.
    assert not fake_root.exists()
    # _relative_to_library lexically: existing absolute path under fake_root
    from muzilla.pipeline.paths import _relative_to_library

    assert _relative_to_library(str(fake_root / "a.mp3"), fake_root) == "a.mp3"
    assert _relative_to_library(str(fake_root / "subdir" / "b.mp3"), fake_root) == "subdir/b.mp3"
    assert _relative_to_library("/other/root/file.mp3", fake_root) == "other/root/file.mp3"
    assert _relative_to_library("already/relative.mp3", fake_root) == "already/relative.mp3"
    assert _relative_to_library(str(fake_root), fake_root) == ""
