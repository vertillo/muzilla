from __future__ import annotations

import itertools
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy.orm import Session

from muzilla.audio.art import ArtProcessingError, ProcessedArt
from muzilla.audio.replaygain import TrackReplayGain
from muzilla.changes.blobstore import BlobStore
from muzilla.db.models import ChangeSet, Track, TrackGroup
from muzilla.domain.metadata import LyricsResult
from muzilla.pipeline.enrichment import (
    fetch_and_process_art,
    groups_needing_art,
    groups_needing_replaygain,
    stage_art_for_group,
    stage_lyrics_for_track,
    stage_replaygain_for_group,
    tracks_needing_lyrics,
)
from muzilla.providers.base import ArtRef

_group_key_counter = itertools.count()


def _make_group(session: Session, *, kind: str = "album", mb_release_id: str | None = None) -> TrackGroup:
    group = TrackGroup(
        key=f"key-{kind}-{next(_group_key_counter)}",
        kind=kind,
        album="Test Album",
        mb_release_id=mb_release_id,
    )
    session.add(group)
    session.flush()
    return group


def _make_track(
    session: Session,
    group: TrackGroup | None,
    *,
    path: str,
    title: str,
    artist: str | None = None,
    has_embedded_art: bool = False,
    has_lyrics: bool = False,
) -> Track:
    t = Track(
        path=path,
        filename=Path(path).name,
        ext=".flac",
        size_bytes=1000,
        mtime_ns=1,
        title=title,
        artist=artist,
        group_id=group.id if group is not None else None,
        has_embedded_art=has_embedded_art,
        has_lyrics=has_lyrics,
    )
    session.add(t)
    session.flush()
    return t


def test_groups_needing_replaygain_returns_group_with_unanalyzed_track(db_session: Session) -> None:
    group = _make_group(db_session)
    _make_track(db_session, group, path="/music/a.flac", title="A")
    db_session.commit()

    groups = groups_needing_replaygain(db_session)

    assert [g.id for g in groups] == [group.id]


def test_groups_needing_replaygain_excludes_fully_analyzed_group(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    assert groups_needing_replaygain(db_session) == []


def test_groups_needing_replaygain_excludes_missing_tracks(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.missing_since = datetime.now(UTC)
    db_session.commit()

    assert groups_needing_replaygain(db_session) == []


def test_stage_replaygain_for_group_sets_track_and_album_fields(db_session: Session) -> None:
    group = _make_group(db_session)
    a = _make_track(db_session, group, path="/music/a.flac", title="A")
    b = _make_track(db_session, group, path="/music/b.flac", title="B")
    db_session.commit()

    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-3.2, track_peak=0.9,
            album_gain_db=-2.5, album_peak=0.95,
        ),
        Path("/music/b.flac"): TrackReplayGain(
            path=Path("/music/b.flac"), track_gain_db=-4.0, track_peak=0.8,
            album_gain_db=-2.5, album_peak=0.95,
        ),
    }
    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value=results):
        change_set = stage_replaygain_for_group(db_session, group)
    db_session.commit()

    assert change_set is not None
    assert change_set.source == "enrichment"
    assert change_set.state == "draft"
    assert change_set.scope_type == "group"
    assert change_set.scope_id == group.id

    by_entity: dict[int, dict[str, object]] = {}
    for change in change_set.changes:
        by_entity.setdefault(change.entity_id, {})[change.field] = change.new_value
        assert change.decision == "accepted"

    assert by_entity[a.id]["rg_track_gain"] == -3.2
    assert by_entity[a.id]["rg_album_gain"] == -2.5
    assert by_entity[b.id]["rg_track_gain"] == -4.0


def test_stage_replaygain_for_group_skips_when_no_tracks_need_it(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    with patch("muzilla.pipeline.enrichment.compute_album_replaygain") as mock_compute:
        result = stage_replaygain_for_group(db_session, group)

    assert result is None
    mock_compute.assert_not_called()


def test_stage_replaygain_for_group_force_reanalyzes_everything(db_session: Session) -> None:
    group = _make_group(db_session)
    track = _make_track(db_session, group, path="/music/a.flac", title="A")
    track.rg_track_gain = -3.0
    db_session.commit()

    results = {
        Path("/music/a.flac"): TrackReplayGain(
            path=Path("/music/a.flac"), track_gain_db=-5.0, track_peak=0.7
        ),
    }
    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value=results):
        change_set = stage_replaygain_for_group(db_session, group, force=True)
    db_session.commit()

    assert change_set is not None
    assert change_set.changes[0].new_value == -5.0


def test_stage_replaygain_for_group_returns_none_when_rsgain_has_no_results(
    db_session: Session,
) -> None:
    group = _make_group(db_session)
    _make_track(db_session, group, path="/music/a.flac", title="A")
    db_session.commit()

    with patch("muzilla.pipeline.enrichment.compute_album_replaygain", return_value={}):
        result = stage_replaygain_for_group(db_session, group)

    assert result is None


# ---------------------------------------------------------------- art --


def test_groups_needing_art_requires_mb_release_id(db_session: Session) -> None:
    with_release = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, with_release, path="/music/a.flac", title="A")
    without_release = _make_group(db_session, mb_release_id=None)
    _make_track(db_session, without_release, path="/music/b.flac", title="B")
    db_session.commit()

    groups = groups_needing_art(db_session, prefer_existing=True)

    assert [g.id for g in groups] == [with_release.id]


def test_groups_needing_art_excludes_group_with_art_blob_already(db_session: Session) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, group, path="/music/a.flac", title="A")
    group.art_blob_id = 1
    db_session.commit()

    assert groups_needing_art(db_session, prefer_existing=True) == []


def test_groups_needing_art_prefer_existing_skips_fully_embedded_group(db_session: Session) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, group, path="/music/a.flac", title="A", has_embedded_art=True)
    db_session.commit()

    assert groups_needing_art(db_session, prefer_existing=True) == []
    assert groups_needing_art(db_session, prefer_existing=False) == [group]


def test_groups_needing_art_prefer_existing_includes_partially_embedded_group(
    db_session: Session,
) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, group, path="/music/a.flac", title="A", has_embedded_art=True)
    _make_track(db_session, group, path="/music/b.flac", title="B", has_embedded_art=False)
    db_session.commit()

    assert groups_needing_art(db_session, prefer_existing=True) == [group]


class _StubArtProvider:
    def __init__(self, refs: list[ArtRef]) -> None:
        self._refs = refs

    async def get_art(self, ref: object) -> list[ArtRef]:
        return self._refs


def _ok_response(content: bytes, url: str = "https://example.invalid/cover.jpg") -> httpx.Response:
    return httpx.Response(200, content=content, request=httpx.Request("GET", url))


async def test_fetch_and_process_art_downloads_and_resizes_first_ref() -> None:
    provider = _StubArtProvider([ArtRef(url="https://example.invalid/cover.jpg", source="coverartarchive")])
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _ok_response(b"fake jpeg bytes")

    with patch(
        "muzilla.pipeline.enrichment.process_art",
        return_value=ProcessedArt(data=b"resized", mime="image/jpeg", width=500, height=500),
    ):
        result = await fetch_and_process_art(client, provider, "rel-1", max_dimension=1200)

    assert result == (b"resized", "image/jpeg")


async def test_fetch_and_process_art_returns_none_when_no_refs() -> None:
    provider = _StubArtProvider([])
    client = AsyncMock(spec=httpx.AsyncClient)

    result = await fetch_and_process_art(client, provider, "rel-1", max_dimension=1200)

    assert result is None
    client.get.assert_not_called()


async def test_fetch_and_process_art_falls_through_to_next_ref_on_http_error() -> None:
    provider = _StubArtProvider(
        [
            ArtRef(url="https://example.invalid/bad.jpg", source="coverartarchive"),
            ArtRef(url="https://example.invalid/good.jpg", source="coverartarchive"),
        ]
    )
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = [
        httpx.ConnectError("boom"),
        _ok_response(b"fake jpeg bytes"),
    ]

    with patch(
        "muzilla.pipeline.enrichment.process_art",
        return_value=ProcessedArt(data=b"resized", mime="image/jpeg", width=500, height=500),
    ):
        result = await fetch_and_process_art(client, provider, "rel-1", max_dimension=1200)

    assert result == (b"resized", "image/jpeg")
    assert client.get.call_count == 2


async def test_fetch_and_process_art_falls_through_on_undecodable_image() -> None:
    provider = _StubArtProvider([ArtRef(url="https://example.invalid/cover.jpg", source="coverartarchive")])
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _ok_response(b"not an image")

    with patch("muzilla.pipeline.enrichment.process_art", side_effect=ArtProcessingError("bad")):
        result = await fetch_and_process_art(client, provider, "rel-1", max_dimension=1200)

    assert result is None


def test_stage_art_for_group_embeds_only_tracks_missing_art(db_session: Session, tmp_path: Path) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    a = _make_track(db_session, group, path="/music/a.flac", title="A", has_embedded_art=False)
    b = _make_track(db_session, group, path="/music/b.flac", title="B", has_embedded_art=True)
    db_session.commit()

    store = BlobStore(tmp_path / "blobs")
    change_set = stage_art_for_group(db_session, group, store, b"jpeg bytes", "image/jpeg")
    db_session.commit()

    assert change_set is not None
    assert change_set.source == "enrichment"
    entity_ids = {c.entity_id for c in change_set.changes}
    assert entity_ids == {a.id}
    assert b.id not in entity_ids

    change = change_set.changes[0]
    assert change.op == "embed_art"
    assert change.new_blob_id is not None
    assert change.decision == "accepted"

    db_session.refresh(group)
    assert group.art_blob_id is None


def test_stage_art_for_group_reuses_the_pending_proposal(db_session: Session, tmp_path: Path) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, group, path="/music/a.flac", title="A")
    db_session.commit()

    store = BlobStore(tmp_path / "blobs")
    first = stage_art_for_group(db_session, group, store, b"jpeg bytes", "image/jpeg")
    second = stage_art_for_group(db_session, group, store, b"jpeg bytes", "image/jpeg")

    assert first is not None
    assert second is not None
    assert second.id == first.id
    assert groups_needing_art(db_session, prefer_existing=False) == []
    assert db_session.query(ChangeSet).count() == 1


def test_stage_art_for_group_returns_none_when_all_tracks_already_have_art(
    db_session: Session, tmp_path: Path
) -> None:
    group = _make_group(db_session, mb_release_id="rel-1")
    _make_track(db_session, group, path="/music/a.flac", title="A", has_embedded_art=True)
    db_session.commit()

    store = BlobStore(tmp_path / "blobs")
    result = stage_art_for_group(db_session, group, store, b"jpeg bytes", "image/jpeg")

    assert result is None


# ------------------------------------------------------------- lyrics --


class _StubLyricsProvider:
    def __init__(self, result: LyricsResult | None) -> None:
        self._result = result

    async def get_lyrics(self, artist: str, title: str, duration_ms: int | None) -> LyricsResult | None:
        return self._result


def test_tracks_needing_lyrics_requires_title_and_artist(db_session: Session) -> None:
    with_both = _make_track(db_session, None, path="/music/a.flac", title="A", artist="Artist")
    _make_track(db_session, None, path="/music/b.flac", title="B", artist=None)
    db_session.commit()

    tracks = tracks_needing_lyrics(db_session)

    assert [t.id for t in tracks] == [with_both.id]


def test_tracks_needing_lyrics_excludes_tracks_that_already_have_lyrics(db_session: Session) -> None:
    _make_track(
        db_session, None, path="/music/a.flac", title="A", artist="Artist", has_lyrics=True
    )
    db_session.commit()

    assert tracks_needing_lyrics(db_session) == []


def test_tracks_needing_lyrics_excludes_missing_tracks(db_session: Session) -> None:
    track = _make_track(db_session, None, path="/music/a.flac", title="A", artist="Artist")
    track.missing_since = datetime.now(UTC)
    db_session.commit()

    assert tracks_needing_lyrics(db_session) == []


async def test_stage_lyrics_for_track_stages_write_lyrics_change(db_session: Session) -> None:
    track = _make_track(db_session, None, path="/music/a.flac", title="A", artist="Artist")
    db_session.commit()

    provider = _StubLyricsProvider(
        LyricsResult(text="la la la", synced=False, source="lrclib")
    )
    change_set = await stage_lyrics_for_track(db_session, track, provider)  # type: ignore[arg-type]
    db_session.commit()

    assert change_set is not None
    assert change_set.source == "enrichment"
    change = change_set.changes[0]
    assert change.op == "write_lyrics"
    assert change.new_value == {"text": "la la la", "synced": False}
    assert change.decision == "accepted"


async def test_stage_lyrics_for_track_returns_none_when_no_match(db_session: Session) -> None:
    track = _make_track(db_session, None, path="/music/a.flac", title="A", artist="Artist")
    db_session.commit()

    provider = _StubLyricsProvider(None)
    result = await stage_lyrics_for_track(db_session, track, provider)  # type: ignore[arg-type]

    assert result is None
