from __future__ import annotations

from sqlalchemy.orm import Session

from muzilla.db.models import Track, TrackFingerprintMatch, TrackGroup
from muzilla.pipeline.grouping import run_grouping_cascade


def _make_track(
    session: Session,
    *,
    path: str,
    title: str,
    artist: str | None = None,
    album: str | None = None,
    album_artist: str | None = None,
    track_total: int | None = None,
    mb_release_id: str | None = None,
    barcode: str | None = None,
    catalog_number: str | None = None,
    label: str | None = None,
) -> Track:
    t = Track(
        path=path,
        filename=path.rsplit("/", 1)[-1],
        ext=".mp3",
        size_bytes=1000,
        mtime_ns=1,
        title=title,
        artist=artist,
        album=album,
        album_artist=album_artist,
        track_total=track_total,
        mb_release_id=mb_release_id,
        barcode=barcode,
        catalog_number=catalog_number,
        label=label,
    )
    session.add(t)
    session.flush()
    return t


def test_stage1_groups_by_mb_release_id(db_session: Session) -> None:
    _make_track(
        db_session, path="/a1", title="Track 1", album="X", mb_release_id="11111111-1111-1111-1111-111111111111"
    )
    _make_track(
        db_session, path="/a2", title="Track 2", album="X", mb_release_id="11111111-1111-1111-1111-111111111111"
    )
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    album_groups = [p for p in result.proposals if p.grouping_basis == "release_id"]
    assert len(album_groups) == 1
    assert album_groups[0].grouping_confidence == 1.0
    assert len(album_groups[0].track_ids) == 2


def _add_fingerprint_match(
    session: Session, track: Track, mb_recording_id: str, mb_release_ids: list[str], score: float = 0.9
) -> None:
    session.add(
        TrackFingerprintMatch(
            track_id=track.id,
            mb_recording_id=mb_recording_id,
            mb_release_ids=mb_release_ids,
            score=score,
        )
    )
    session.flush()


def test_stage2_fingerprint_consensus_groups_by_shared_release_mbid(db_session: Session) -> None:
    # No usable album tags at all -- fingerprints are the only signal,
    # exactly the era-varying-tags case Stage 2 exists for.
    t1 = _make_track(db_session, path="/fp1", title="Track 1")
    t2 = _make_track(db_session, path="/fp2", title="Track 2")
    t3 = _make_track(db_session, path="/fp3", title="Track 3")
    db_session.commit()

    release_id = "22222222-2222-2222-2222-222222222222"
    for t in (t1, t2, t3):
        _add_fingerprint_match(db_session, t, f"rec-{t.id}", [release_id])
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    fp_groups = [p for p in result.proposals if p.grouping_basis == "fingerprint"]
    assert len(fp_groups) == 1
    assert fp_groups[0].grouping_confidence == 0.9
    assert fp_groups[0].mb_release_id == release_id
    assert set(fp_groups[0].track_ids) == {t1.id, t2.id, t3.id}


def test_stage2_requires_consensus_not_a_single_fingerprint_match(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/fp4", title="Lone Track")
    db_session.commit()
    _add_fingerprint_match(db_session, t1, "rec-x", ["33333333-3333-3333-3333-333333333333"])
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    fp_groups = [p for p in result.proposals if p.grouping_basis == "fingerprint"]
    assert fp_groups == []
    # Falls through to singleton classification instead of being lost.
    singleton_groups = [p for p in result.proposals if p.grouping_basis == "singleton"]
    assert len(singleton_groups) == 1


def test_stage2_ignores_tracks_with_no_fingerprint_data(db_session: Session) -> None:
    _make_track(db_session, path="/fp5", title="Unfingerprinted", album="Some Album")
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    fp_groups = [p for p in result.proposals if p.grouping_basis == "fingerprint"]
    assert fp_groups == []


def test_stage3_fuzzy_clusters_similar_album_tags(db_session: Session) -> None:
    _make_track(db_session, path="/b1", title="Song A", artist="The Beatles", album="Abbey Road", album_artist="The Beatles")
    _make_track(db_session, path="/b2", title="Song B", artist="Beatles", album="Abbey Road", album_artist="Beatles")
    _make_track(db_session, path="/b3", title="Song C", artist="Beatles", album="Abbey Road (Remastered)", album_artist="Beatles")
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    tag_groups = [p for p in result.proposals if p.grouping_basis == "tags"]
    assert len(tag_groups) == 1
    assert len(tag_groups[0].track_ids) == 3
    assert 0.5 <= tag_groups[0].grouping_confidence <= 0.85


def test_stage4_classifies_loose_tracks_as_singletons(db_session: Session) -> None:
    _make_track(db_session, path="/c1", title="Standalone", artist="Someone", album=None)
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    singles = [p for p in result.proposals if p.kind == "singleton"]
    assert len(singles) == 1
    assert singles[0].track_ids == (
        db_session.query(Track).filter(Track.path == "/c1").one().id,
    )


def test_album_equals_title_is_singleton() -> None:
    from muzilla.pipeline.grouping import _is_singleton_track

    t = Track(
        path="/x", filename="x.mp3", ext=".mp3", size_bytes=1, mtime_ns=1,
        title="Some Song", album="Some Song",
    )
    assert _is_singleton_track(t) is True


def test_track_total_one_is_singleton() -> None:
    from muzilla.pipeline.grouping import _is_singleton_track

    t = Track(
        path="/x", filename="x.mp3", ext=".mp3", size_bytes=1, mtime_ns=1,
        title="Some Song", album="Some Album", track_total=1,
    )
    assert _is_singleton_track(t) is True


def test_partial_album_flagged_when_track_total_exceeds_cluster_size(db_session: Session) -> None:
    _make_track(db_session, path="/d1", title="T1", artist="Artist", album="Big Album", album_artist="Artist", track_total=12)
    _make_track(db_session, path="/d2", title="T2", artist="Artist", album="Big Album", album_artist="Artist", track_total=12)
    _make_track(db_session, path="/d3", title="T3", artist="Artist", album="Big Album", album_artist="Artist", track_total=12)
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    partials = [p for p in result.proposals if p.kind == "partial_album"]
    assert len(partials) == 1
    assert partials[0].expected_track_count == 12
    assert len(partials[0].track_ids) == 3


def test_pinned_group_is_never_overwritten(db_session: Session) -> None:
    track = _make_track(db_session, path="/e1", title="T", artist="Artist", album="Album", album_artist="Artist")
    group = TrackGroup(
        key="manual-pin-key",
        kind="album",
        grouping_basis="manual",
        grouping_confidence=1.0,
        is_pinned=True,
        album="Custom Album Name",
    )
    db_session.add(group)
    db_session.flush()
    track.group_id = group.id
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    db_session.refresh(group)
    assert group.album == "Custom Album Name"  # untouched
    assert result.tracks_skipped_pinned == 1


def test_persists_group_rows_and_assigns_tracks(db_session: Session) -> None:
    t1 = _make_track(db_session, path="/f1", title="T1", artist="Artist", album="Album", album_artist="Artist")
    t2 = _make_track(db_session, path="/f2", title="T2", artist="Artist", album="Album", album_artist="Artist")
    db_session.commit()

    result = run_grouping_cascade(db_session)
    db_session.commit()

    assert result.groups_created >= 1
    db_session.refresh(t1)
    db_session.refresh(t2)
    assert t1.group_id is not None
    assert t1.group_id == t2.group_id


def test_cascade_does_not_crash_past_sqlite_variable_limit(db_session: Session) -> None:
    """Regression test for a real bug docs/product-spec.md 100k-track
    performance pass found: the fingerprint-consensus stage queried
    TrackFingerprintMatch with `track_id.in_(remaining_ids)` in one
    unbatched call, which raised `sqlite3.OperationalError: too many
    SQL variables` once remaining_ids exceeded SQLite's variable limit
    (32766 on this build's SQLite 3.53 — older SQLite defaults to 999,
    which is why the fix batches at 500 regardless of what this
    particular build's ceiling happens to be) — never triggered by any
    test before this, since nothing exercised the cascade above
    fixture scale. 35000 tracks, no fingerprint data, is enough to
    exceed the limit and prove the fix's batching holds."""
    for i in range(35_000):
        _make_track(db_session, path=f"/perf/{i}.mp3", title=f"Track {i}", artist=f"Artist {i}")
    db_session.commit()

    result = run_grouping_cascade(db_session)  # must not raise
    db_session.commit()

    assert result.tracks_grouped == 35_000
