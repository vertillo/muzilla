from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from muzilla.config.schema import PathsConfig
from muzilla.db.models import Track, TrackGroup
from muzilla.services import paths as paths_service


def _make_track(session: Session, *, path: str, **kwargs: object) -> Track:
    t = Track(path=path, filename=path.rsplit("/", 1)[-1], ext=".mp3", size_bytes=1, mtime_ns=1, **kwargs)
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
    assert row.new_path == "Artist - Solo"
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
    assert row.new_path == "Band - Album - 1 Track One"


def test_render_path_for_track_no_group_uses_default(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y")
    db_session.commit()

    row = paths_service.render_path_for_track(db_session, t.id, config=_default_config())
    assert row.new_path == "Y - X"


def test_render_path_for_track_missing_track_raises(db_session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        paths_service.render_path_for_track(db_session, 99999, config=_default_config())


def test_render_path_for_track_with_template_override(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y")
    db_session.commit()

    row = paths_service.render_path_for_track(
        db_session, t.id, config=_default_config(), template_override="%upper{$artist}"
    )
    assert row.new_path == "Y"


def test_render_path_for_track_query_override(db_session: Session) -> None:
    t = _make_track(db_session, path="/x.mp3", title="X", artist="Y", genre=["Classical"])
    db_session.commit()

    config = _default_config(overrides={"genre:Classical": "Classical/$artist"})
    row = paths_service.render_path_for_track(db_session, t.id, config=config)
    assert row.new_path == "Classical/Y"


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
    assert {r.new_path for r in rows} == {"Band - Al - 1 T1", "Band - Al - 2 T2"}
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
    _make_track(db_session, path="Y - X", title="Untouched", artist="Other")
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
    assert paths_by_track[t1.id] == "Band - Best Of [1999] - 1 T"
    assert paths_by_track[t2.id] == "Band - Best Of [2010] - 1 T"


def test_no_aunique_collision_renders_empty_bracket(db_session: Session) -> None:
    g = _make_group(db_session, kind="album", album="Unique Album", album_artist="Band", year=1999)
    t = _make_track(db_session, path="/x.mp3", title="T", track_no=1, album="Unique Album", album_artist="Band")
    t.group_id = g.id
    db_session.commit()

    config = _default_config(album="$albumartist - $album%aunique{} - $track $title")
    rows = paths_service.preview_rename(db_session, track_ids=[t.id], config=config)
    assert rows[0].new_path == "Band - Unique Album - 1 T"


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
    assert change.new_value == "Y - X"
    assert change.old_value == "/old.mp3"


def test_stage_rename_skips_already_correct_tracks(db_session: Session) -> None:
    t1 = _make_track(db_session, path="Y - X", title="X", artist="Y")
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
    t = _make_track(db_session, path="Y - X", title="X", artist="Y")
    db_session.commit()

    with pytest.raises(paths_service.PathValidationError, match="already"):
        paths_service.stage_rename(db_session, track_ids=[t.id], config=_default_config())
