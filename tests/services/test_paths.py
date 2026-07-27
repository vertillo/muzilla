from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.models import Track, TrackGroup
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
    g = TrackGroup(key=key, **kwargs)  # type: ignore[arg-type]
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
        db_session, path="/a1.mp3", title="Track One", track_no=1,
        album="Album", album_artist="Band",
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
    """create_directories=True: the override template's "Classical/"
    prefix is a real directory split, not a stray literal `/` -- which
    is exactly what the pre-fix version of this test never actually
    exercised. Without create_directories, paths/render.py correctly
    flags a rendered `/` as a validation error (proven directly, not
    just asserted): this test's template would otherwise silently
    "succeed" into row.errors being non-empty and new_path being an
    error-path artifact, which the original assertion never checked."""
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
    t1 = _make_track(db_session, path="/a1.mp3", title="T1", track_no=1, album="Al", album_artist="Band")
    t2 = _make_track(db_session, path="/a2.mp3", title="T2", track_no=2, album="Al", album_artist="Band")
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

    rows = paths_service.preview_rename(
        db_session, track_ids=[mover.id], config=_default_config()
    )
    assert len(rows) == 1
    assert rows[0].is_collision is True


def test_preview_rename_no_tracks_returns_empty(db_session: Session) -> None:
    rows = paths_service.preview_rename(db_session, track_ids=[], config=_default_config())
    assert rows == []


def test_preview_rename_missing_group_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        paths_service.preview_rename(db_session, group_id=99999, config=_default_config())


# --- %aunique disambiguation via DbDisambiguationResolver -----------------------


def test_aunique_resolves_via_year_across_colliding_albums(db_session: Session) -> None:
    g1 = _make_group(db_session, kind="album", album="Best Of", album_artist="Band", year=1999)
    g2 = _make_group(db_session, kind="album", album="Best Of", album_artist="Band", year=2010)
    t1 = _make_track(
        db_session, path="/g1.mp3", title="T", track_no=1, album="Best Of",
        album_artist="Band", year=1999,
    )
    t2 = _make_track(
        db_session, path="/g2.mp3", title="T", track_no=1, album="Best Of",
        album_artist="Band", year=2010,
    )
    t1.group_id = g1.id
    t2.group_id = g2.id
    db_session.commit()

    config = _default_config(album="$albumartist - $album%aunique{} - $track $title")
    rows = paths_service.preview_rename(
        db_session, track_ids=[t1.id, t2.id], config=config
    )
    paths_by_track = {r.track_id: r.new_path for r in rows}
    # %aunique's resolver sees both projected groups sharing the same
    # (albumartist, album) key, picks year as the separating field, and
    # each track's own year value is substituted into the bracket.
    assert paths_by_track[t1.id] == "Band - Best Of [1999] - 1 T.mp3"
    assert paths_by_track[t2.id] == "Band - Best Of [2010] - 1 T.mp3"


def test_no_aunique_collision_renders_empty_bracket(db_session: Session) -> None:
    g = _make_group(db_session, kind="album", album="Unique Album", album_artist="Band", year=1999)
    t = _make_track(db_session, path="/x.mp3", title="T", track_no=1, album="Unique Album", album_artist="Band")
    t.group_id = g.id
    db_session.commit()

    config = _default_config(album="$albumartist - $album%aunique{} - $track $title")
    rows = paths_service.preview_rename(db_session, track_ids=[t.id], config=config)
    assert rows[0].new_path == "Band - Unique Album - 1 T.mp3"


# --- stage_rename ----------------------------------------------------------------


def test_stage_rename_creates_move_changeset(db_session: Session) -> None:
    t = _make_track(db_session, path="/old.mp3", title="X", artist="Y")
    db_session.commit()

    cs = paths_service.stage_rename(
        db_session, track_ids=[t.id], config=_default_config()
    )
    db_session.commit()

    assert cs.source == "rename"
    assert len(cs.changes) == 1
    change = cs.changes[0]
    assert change.field == "path"
    assert change.op == "move"
    assert change.new_value == "Y - X.mp3"
    assert change.old_value == "/old.mp3"


def test_stage_rename_skips_already_correct_tracks(db_session: Session) -> None:
    t1 = _make_track(db_session, path="Y - X.mp3", title="X", artist="Y")
    t2 = _make_track(db_session, path="/needs-rename.mp3", title="Z", artist="Y")
    db_session.commit()

    cs = paths_service.stage_rename(
        db_session, track_ids=[t1.id, t2.id], config=_default_config()
    )
    db_session.commit()

    entity_ids = {c.entity_id for c in cs.changes}
    assert entity_ids == {t2.id}


def test_stage_rename_refuses_on_collision(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/a.mp3", title="Same", artist="X")
    t2 = _make_track(db_session, path="/b.mp3", title="Same", artist="X")
    db_session.commit()

    with pytest.raises(paths_service.PathValidationError):
        paths_service.stage_rename(
            db_session, track_ids=[t1.id, t2.id], config=_default_config()
        )


def test_stage_rename_refuses_on_render_error(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X")
    db_session.commit()

    with pytest.raises(paths_service.PathValidationError):
        paths_service.stage_rename(
            db_session,
            track_ids=[t.id],
            config=_default_config(),
            template_override="%time{$missing,%%Y}",
        )


def test_stage_rename_all_already_correct_raises(db_session: Session) -> None:
    t = _make_track(db_session, path="Y - X.mp3", title="X", artist="Y")
    db_session.commit()

    with pytest.raises(paths_service.PathValidationError, match="already"):
        paths_service.stage_rename(db_session, track_ids=[t.id], config=_default_config())


# --- extension preservation ------------------------------------------------------


def test_rendered_path_preserves_source_file_extension(db_session: Session) -> None:
    """Regression test for a real bug found by the §11e E2E rename
    test: paths/render.py has no concept of file extensions by design
    (same as beets' template language, and every example template in
    docs/PLAN.md §6 / config/defaults.yaml omits one), so without this
    every rename silently produced an extensionless, unplayable file.
    Fixed in services/paths.py's _with_extension, sourced from
    Track.ext (which always includes the leading dot -- pipeline/
    scan.py populates it from Path.suffix)."""
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


# --- performance (docs/PLAN.md §11g) ---------------------------------------------


def test_preview_rename_does_not_issue_one_query_per_track_for_group_kind(
    db_session: Session,
) -> None:
    """Regression test for a real N+1 docs/PLAN.md §11g's 100k-track
    performance pass found (and had already flagged by inspection
    before measuring): preview_rename previously called
    `_group_kind(session, track.group_id)` -- one `session.get()` per
    track -- inside its per-track loop. Fixed with `_group_kinds_by_id`,
    a single batched query before the loop. Counts SQL statements via
    SQLAlchemy's event hook rather than just timing, so this test
    fails deterministically on a regression instead of only on a slow
    CI runner."""
    from sqlalchemy import event

    # One DISTINCT group per track: session.get()'s identity-map cache
    # makes repeated per-track calls for the *same* group_id free after
    # the first, which would silently mask the N+1 if all tracks shared
    # only a couple of groups (a smaller, shared-group version of this
    # test passed even against the pre-fix code, for exactly that reason
    # -- caught only by widening it to one group per track).
    tracks = []
    for i in range(30):
        g = _make_group(db_session, kind="album", album=f"Al{i}", album_artist=f"Band{i}")
        t = _make_track(
            db_session, path=f"/t{i}.mp3", title=f"T{i}", track_no=1,
            album=f"Al{i}", album_artist=f"Band{i}",
        )
        t.group_id = g.id
        tracks.append(t)
    db_session.commit()

    statement_count = 0

    def _count(conn, cursor, statement, parameters, context, executemany):
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
    # A per-track query for group kind (30 distinct groups, no identity-
    # map reuse possible) would put this well over 30; the fixed version
    # issues a small, track-count-independent number of batched queries.
    assert statement_count < 20, (
        f"expected a small, batch-scoped query count, got {statement_count} "
        f"statements for 30 tracks across 30 distinct groups"
    )


def test_preview_rename_collision_check_does_not_load_full_track_rows(
    db_session: Session,
) -> None:
    """Regression test for the dominant cost docs/PLAN.md §11g's
    performance pass found: the collision check loaded full ORM
    `Track` objects (JSON-column genre/artists/mood deserialization
    included) for every OTHER track in the library just to build a
    path->id dict. Fixed with a column-scoped `select(Track.id,
    Track.path)`. Proven here by seeding a genre value that would
    raise if the row were ever instantiated as a full Track through a
    code path that mishandles it, and confirming the preview still
    completes correctly -- but the real proof is the query shape,
    checked via `str(statement)` containing only the two columns."""
    from sqlalchemy import event

    other = _make_track(db_session, path="/other.mp3", title="Other", artist="X", genre=["A", "B"])
    mover = _make_track(db_session, path="/mover.mp3", title="Y", artist="Z")
    db_session.commit()

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.get_bind(), "before_cursor_execute", _capture)
    try:
        rows = paths_service.preview_rename(
            db_session, track_ids=[mover.id], config=_default_config()
        )
    finally:
        event.remove(db_session.get_bind(), "before_cursor_execute", _capture)

    assert len(rows) == 1
    collision_queries = [s for s in statements if "notin" in s.lower() or "not in" in s.lower()]
    assert collision_queries, "expected a NOT IN query for the collision check"
    # The fixed query selects only id and path; a regression back to
    # select(Track) would pull every mapped column, including genre.
    assert "genre" not in collision_queries[0].lower()
    _ = other  # exists only to give the library-scan something to (not) load in full
