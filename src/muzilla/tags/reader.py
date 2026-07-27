"""Reads audio files into TrackMeta + technical probe data via mutagen.

Dispatches per-format because each container's tag scheme needs
different extraction logic (ID3 frames, Vorbis comments, MP4 atoms).
Corrupt or unreadable files raise TagReadError rather than crashing —
callers (the scan pipeline) catch this and continue past one bad file.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from muzilla.domain.ids import normalize_barcode, normalize_isrc, normalize_mbid
from muzilla.domain.metadata import TrackMeta
from muzilla.tags.mapping import (
    ID3_FRAMES,
    MP4_FREEFORM_KEYS,
    MP4_FREEFORM_MEAN,
    MP4_STANDARD_KEYS,
    VORBIS_KEYS,
)


class TagReadError(Exception):
    def __init__(self, path: Path, cause: Exception) -> None:
        super().__init__(f"failed to read tags from {path}: {cause}")
        self.path = path
        self.cause = cause


def read_track(path: Path) -> TrackMeta:
    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:  # mutagen raises a wide variety of error types
        raise TagReadError(path, exc) from exc

    if audio is None:
        raise TagReadError(path, ValueError("unrecognized or unreadable format"))

    # Dispatch on the TAGS class, not the container class: WAVE/AIFF wrap
    # an embedded ID3 chunk (mutagen.wave._WaveID3 / _IFFID3 both subclass
    # ID3), so they must be read with the ID3 path even though the file
    # container itself isn't an ID3FileType.
    if isinstance(audio.tags, ID3):
        meta = _read_id3(audio)
    elif isinstance(audio, MP4):
        meta = _read_mp4(audio)
    elif isinstance(audio, FLAC | OggVorbis | OggOpus):
        meta = _read_vorbis(audio)
    else:
        meta = _read_vorbis(audio) if audio.tags is not None else TrackMeta()

    return _apply_probe(meta, audio)


def _has_embedded_art(audio: Any) -> bool:
    """Whether the already-open mutagen file has at least one embedded
    picture — checked here (not a standalone function) so scanning
    100k files doesn't need a second mutagen.File() open per track just
    to answer this. Same per-format dispatch as write_art/clear_art in
    tags/writer.py, mirrored here for read. Used to populate
    `Track.has_embedded_art` and, by enrichment, to decide whether a
    track needs art fetched at all (docs/PLAN.md §9: "keep existing" is
    the default since local art is often better than a provider's)."""
    if isinstance(audio.tags, ID3):
        return bool(audio.tags.getall("APIC"))  # type: ignore[no-untyped-call]
    if isinstance(audio, MP4):
        return audio.tags is not None and "covr" in audio.tags
    if isinstance(audio, FLAC):
        return bool(audio.pictures)
    if isinstance(audio, OggVorbis | OggOpus):
        return audio.tags is not None and "metadata_block_picture" in audio.tags
    return False


def _apply_probe(meta: TrackMeta, audio: Any) -> TrackMeta:
    info = audio.info
    duration_ms = round(getattr(info, "length", 0.0) * 1000) or None
    bitrate = getattr(info, "bitrate", None)
    sample_rate = getattr(info, "sample_rate", None)
    channels = getattr(info, "channels", None)
    codec = type(audio).__name__
    return replace(
        meta,
        duration_ms=duration_ms,
        bitrate=bitrate,
        sample_rate=sample_rate,
        channels=channels,
        codec=codec,
        mb_track_id=normalize_mbid(meta.mb_track_id),
        mb_release_id=normalize_mbid(meta.mb_release_id),
        mb_recording_id=normalize_mbid(meta.mb_recording_id),
        mb_artist_id=normalize_mbid(meta.mb_artist_id),
        isrc=normalize_isrc(meta.isrc),
        barcode=normalize_barcode(meta.barcode),
        has_embedded_art=_has_embedded_art(audio),
    )


def _split_num_total(raw: str | None) -> tuple[int | None, int | None]:
    if not raw:
        return None, None
    parts = raw.split("/", 1)
    try:
        no = int(parts[0]) if parts[0].strip() else None
    except ValueError:
        no = None
    total = None
    if len(parts) > 1:
        try:
            total = int(parts[1]) if parts[1].strip() else None
        except ValueError:
            total = None
    return no, total


def _to_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def _to_float(raw: str | None) -> float | None:
    if not raw:
        return None
    # ReplayGain values are commonly stored as "-6.40 dB"
    cleaned = raw.strip().split(" ")[0]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_year(date_str: str | None) -> int | None:
    if not date_str:
        return None
    prefix = date_str[:4]
    return int(prefix) if prefix.isdigit() else None


# ---------------------------------------------------------------- Vorbis --


def _read_vorbis(audio: Any) -> TrackMeta:
    tags = audio.tags or {}
    kv: dict[str, str] = {}
    multi: dict[str, tuple[str, ...]] = {}
    for canonical, key in VORBIS_KEYS.items():
        values = tags.get(key)
        if not values:
            continue
        if canonical == "artists":
            multi["artists"] = tuple(str(v) for v in values)
        kv[canonical] = str(values[0])

    genre_values = tags.get("GENRE")
    mood_values = tags.get("MOOD")

    track_no = _to_int(kv.get("track_no"))
    track_total = _to_int(kv.get("track_total"))
    disc_no = _to_int(kv.get("disc_no"))
    disc_total = _to_int(kv.get("disc_total"))
    date = kv.get("date")

    return TrackMeta(
        title=kv.get("title"),
        artist=kv.get("artist"),
        artists=multi.get("artists", ()),
        album=kv.get("album"),
        album_artist=kv.get("album_artist"),
        composer=kv.get("composer"),
        track_no=track_no,
        track_total=track_total,
        disc_no=disc_no,
        disc_total=disc_total,
        year=_extract_year(date),
        original_year=_to_int(kv.get("original_year")),
        date=date,
        compilation=kv.get("compilation") in ("1", "true", "yes"),
        label=kv.get("label"),
        catalog_number=kv.get("catalog_number"),
        barcode=kv.get("barcode"),
        isrc=kv.get("isrc"),
        country=kv.get("country"),
        media=kv.get("media"),
        genre=tuple(str(g) for g in genre_values) if genre_values else (),
        mood=tuple(str(m) for m in mood_values) if mood_values else (),
        bpm=_to_int(kv.get("bpm")),
        key=kv.get("key"),
        mb_track_id=kv.get("mb_track_id"),
        mb_release_id=kv.get("mb_release_id"),
        mb_recording_id=kv.get("mb_recording_id"),
        mb_artist_id=kv.get("mb_artist_id"),
        discogs_release_id=kv.get("discogs_release_id"),
        deezer_track_id=kv.get("deezer_track_id"),
        acoustid_id=kv.get("acoustid_id"),
        acoustid_fingerprint=kv.get("acoustid_fingerprint"),
        rg_track_gain=_to_float(kv.get("rg_track_gain")),
        rg_track_peak=_to_float(kv.get("rg_track_peak")),
        rg_album_gain=_to_float(kv.get("rg_album_gain")),
        rg_album_peak=_to_float(kv.get("rg_album_peak")),
        comment=kv.get("comment"),
        encoder=kv.get("encoder"),
    )


# ------------------------------------------------------------------ MP4 --


def _mp4_freeform(tags: Any, name: str) -> str | None:
    key = f"----:{MP4_FREEFORM_MEAN}:{name}"
    values = tags.get(key)
    if not values:
        return None
    raw = values[0]
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


def _read_mp4(audio: MP4) -> TrackMeta:
    tags: Any = audio.tags or {}

    def std(name: str) -> str | None:
        v = tags.get(MP4_STANDARD_KEYS[name])
        if not v:
            return None
        return str(v[0]) if isinstance(v, list) else str(v)

    track_no: int | None = None
    track_total: int | None = None
    if tags.get("trkn"):
        track_no, track_total = tags["trkn"][0]
    disc_no: int | None = None
    disc_total: int | None = None
    if tags.get("disk"):
        disc_no, disc_total = tags["disk"][0]

    genre_raw = tags.get(MP4_STANDARD_KEYS["genre"])
    genre = tuple(str(g) for g in genre_raw) if genre_raw else ()

    date = std("date")
    freeform = {k: _mp4_freeform(tags, name) for k, name in MP4_FREEFORM_KEYS.items()}

    return TrackMeta(
        title=std("title"),
        artist=std("artist"),
        album=std("album"),
        album_artist=std("album_artist"),
        composer=std("composer"),
        track_no=track_no,
        track_total=track_total or None,
        disc_no=disc_no,
        disc_total=disc_total or None,
        year=_extract_year(date),
        date=date,
        compilation=bool(tags.get(MP4_STANDARD_KEYS["compilation"], False)),
        genre=genre,
        bpm=int(tags[MP4_STANDARD_KEYS["bpm"]][0]) if MP4_STANDARD_KEYS["bpm"] in tags else None,
        comment=std("comment"),
        encoder=std("encoder"),
        label=freeform["label"],
        catalog_number=freeform["catalog_number"],
        barcode=freeform["barcode"],
        isrc=freeform["isrc"],
        country=freeform["country"],
        media=freeform["media"],
        mood=(freeform["mood"],) if freeform["mood"] else (),
        key=freeform["key"],
        mb_track_id=freeform["mb_track_id"],
        mb_release_id=freeform["mb_release_id"],
        mb_recording_id=freeform["mb_recording_id"],
        mb_artist_id=freeform["mb_artist_id"],
        discogs_release_id=freeform["discogs_release_id"],
        deezer_track_id=freeform["deezer_track_id"],
        acoustid_id=freeform["acoustid_id"],
        acoustid_fingerprint=freeform["acoustid_fingerprint"],
        rg_track_gain=_to_float(freeform["rg_track_gain"]),
        rg_track_peak=_to_float(freeform["rg_track_peak"]),
        rg_album_gain=_to_float(freeform["rg_album_gain"]),
        rg_album_peak=_to_float(freeform["rg_album_peak"]),
    )


# ------------------------------------------------------------------ ID3 --


def _id3_text(tags: Any, frame_id: str) -> str | None:
    frame = tags.get(frame_id)
    if frame is None:
        return None
    if hasattr(frame, "text") and frame.text:
        return str(frame.text[0])
    return str(frame)


def _read_id3(audio: Any) -> TrackMeta:
    tags = audio.tags
    if tags is None:
        return TrackMeta()

    track_no, track_total = _split_num_total(_id3_text(tags, ID3_FRAMES["track_no"]))
    disc_no, disc_total = _split_num_total(_id3_text(tags, ID3_FRAMES["disc_no"]))
    date = _id3_text(tags, ID3_FRAMES["date"])
    genre_raw = _id3_text(tags, ID3_FRAMES["genre"])
    mood_raw = _id3_text(tags, ID3_FRAMES["mood"])

    mb_recording_id = None
    ufid = tags.get(ID3_FRAMES["mb_recording_id"])
    if ufid is not None and getattr(ufid, "data", None):
        mb_recording_id = ufid.data.decode("ascii", errors="ignore")

    return TrackMeta(
        title=_id3_text(tags, ID3_FRAMES["title"]),
        artist=_id3_text(tags, ID3_FRAMES["artist"]),
        album=_id3_text(tags, ID3_FRAMES["album"]),
        album_artist=_id3_text(tags, ID3_FRAMES["album_artist"]),
        composer=_id3_text(tags, ID3_FRAMES["composer"]),
        track_no=track_no,
        track_total=track_total,
        disc_no=disc_no,
        disc_total=disc_total,
        year=_extract_year(date),
        original_year=_to_int(_id3_text(tags, ID3_FRAMES["original_year"])),
        date=date,
        compilation=_id3_text(tags, ID3_FRAMES["compilation"]) in ("1", "true"),
        label=_id3_text(tags, ID3_FRAMES["label"]),
        catalog_number=_id3_text(tags, ID3_FRAMES["catalog_number"]),
        barcode=_id3_text(tags, ID3_FRAMES["barcode"]),
        isrc=_id3_text(tags, ID3_FRAMES["isrc"]),
        country=_id3_text(tags, ID3_FRAMES["country"]),
        media=_id3_text(tags, ID3_FRAMES["media"]),
        genre=(genre_raw,) if genre_raw else (),
        mood=(mood_raw,) if mood_raw else (),
        bpm=_to_int(_id3_text(tags, ID3_FRAMES["bpm"])),
        key=_id3_text(tags, ID3_FRAMES["key"]),
        mb_track_id=_id3_text(tags, ID3_FRAMES["mb_track_id"]),
        mb_release_id=_id3_text(tags, ID3_FRAMES["mb_release_id"]),
        mb_recording_id=mb_recording_id,
        mb_artist_id=_id3_text(tags, ID3_FRAMES["mb_artist_id"]),
        discogs_release_id=_id3_text(tags, ID3_FRAMES["discogs_release_id"]),
        deezer_track_id=_id3_text(tags, ID3_FRAMES["deezer_track_id"]),
        acoustid_id=_id3_text(tags, ID3_FRAMES["acoustid_id"]),
        acoustid_fingerprint=_id3_text(tags, ID3_FRAMES["acoustid_fingerprint"]),
        rg_track_gain=_to_float(_id3_text(tags, ID3_FRAMES["rg_track_gain"])),
        rg_track_peak=_to_float(_id3_text(tags, ID3_FRAMES["rg_track_peak"])),
        rg_album_gain=_to_float(_id3_text(tags, ID3_FRAMES["rg_album_gain"])),
        rg_album_peak=_to_float(_id3_text(tags, ID3_FRAMES["rg_album_peak"])),
        comment=_id3_text(tags, ID3_FRAMES["comment"]),
        encoder=_id3_text(tags, ID3_FRAMES["encoder"]),
    )
