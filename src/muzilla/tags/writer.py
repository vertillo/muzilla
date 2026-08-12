"""Writes field-level tag updates back to audio files via mutagen.

Mirrors reader.py's per-format dispatch so read and write never drift:
the same VORBIS_KEYS/MP4_*/ID3_FRAMES tables in tags/mapping.py drive
both directions. Only `changes/applier.py` calls this — it is the one
place tags are ever written to disk, always inside the journaled,
atomic-replace apply path (docs/product-spec.md). This module itself knows
nothing about journals or atomicity; it just mutates an already-open
mutagen file object's tags and leaves saving to the caller so the
applier controls the tmp-file-then-os.replace dance.
"""

from __future__ import annotations

import base64
from typing import Any

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    TCMP,
    TCON,
    TDRC,
    TMOO,
    TPOS,
    TRCK,
    TXXX,
    UFID,
    USLT,
    Encoding,
)
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from muzilla.tags.mapping import (
    ID3_FRAMES,
    MP4_FREEFORM_KEYS,
    MP4_FREEFORM_MEAN,
    MP4_LYRICS_KEY,
    MP4_STANDARD_KEYS,
    VORBIS_KEYS,
    VORBIS_LYRICS_KEY,
)
from muzilla.tags.reader import read_track

# Fields tags/reader.py never populates from a real tag frame (probe-only
# technical fields) — writer.py must never attempt to write these.
_READ_ONLY_FIELDS = frozenset(
    {"duration_ms", "bitrate", "sample_rate", "channels", "codec"}
)

# Multi-valued fields (domain.fields FieldType.MULTI_TEXT).
_MULTI_VALUE_FIELDS = frozenset({"artists", "genre", "mood"})


class TagWriteError(Exception):
    def __init__(self, path: Any, cause: Exception) -> None:
        super().__init__(f"failed to write tags to {path}: {cause}")
        self.path = path
        self.cause = cause


def _year_to_date(year: int | None, current_date: str | None) -> str | None:
    """Translates a `year` write into the `date` value that should
    actually be written: `None` clears `date` entirely; an int replaces
    just the leading 4 digits of `current_date` if it has finer
    precision (e.g. "1999-06-12" + year=2005 -> "2005-06-12"), or writes
    a bare 4-digit year if there's no existing date to preserve
    month/day from. `current_date` is the file's date as of the start
    of this write — the caller (`write_fields`) is responsible for
    reading it, so this function does no I/O and cannot itself fail."""
    if year is None:
        return None
    if current_date and len(current_date) > 4:
        return f"{year:04d}{current_date[4:]}"
    return f"{year:04d}"


def write_fields(path: Any, field_values: dict[str, Any]) -> None:
    """Apply `field_values` (canonical field name -> new value, or None
    to clear) to the file at `path` and save it in place.

    Callers are responsible for atomicity (tmp-file + os.replace) — see
    changes/applier.py. This function performs a plain in-place mutagen
    save so it can also be used against an already-relocated tmp copy.

    `year` is not a mapped tag frame in any format — tags/reader.py
    derives it by reading the first 4 digits of `date` (tags/
    mapping.py has no VORBIS_KEYS/ID3_FRAMES/MP4 entry for "year" at
    all). Writing `year` directly used to silently do nothing on every
    format (found by a Hypothesis property test, docs/product-spec.md) —
    translated here into a `date` write instead, preserving any
    existing month/day precision rather than overwriting the whole
    field with a bare year.

    If `field_values` also contains an explicit `date`, that wins and
    the `year` translation is skipped entirely (docs/product-spec.md):
    `date` is the more precise, more specific edit — a caller that
    staged both a `year` change and a `date` change for the same track
    clearly wants the explicit date, not the year translation silently
    overwriting it back to a bare-year (or stale-month/day) value.

    The read needed to know the file's *current* date (so a year edit
    can preserve existing month/day precision) happens exactly once,
    here, via `read_track` — not inside a broad `except Exception` that
    could otherwise turn a real read failure into a silent loss of
    date precision. A failure here is a genuine problem with the file
    and should surface as a `TagWriteError`, same as any other write
    failure, not be swallowed.
    """
    field_values = dict(field_values)
    if "year" in field_values:
        year = field_values.pop("year")
        if "date" not in field_values:
            try:
                current_date = read_track(path).date
            except Exception as exc:
                raise TagWriteError(path, exc) from exc
            field_values["date"] = _year_to_date(year, current_date)

    for field in field_values:
        if field in _READ_ONLY_FIELDS:
            raise ValueError(f"field {field!r} is read-only (probe data), cannot write")

    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:
        raise TagWriteError(path, exc) from exc

    if audio is None:
        raise TagWriteError(path, ValueError("unrecognized or unreadable format"))

    try:
        if isinstance(audio.tags, ID3):
            _write_id3(audio, field_values)
        elif isinstance(audio, MP4):
            _write_mp4(audio, field_values)
        elif isinstance(audio, FLAC | OggVorbis | OggOpus):
            _write_vorbis(audio, field_values)
        else:
            raise TagWriteError(path, ValueError(f"unsupported format: {type(audio).__name__}"))
        audio.save()
    except TagWriteError:
        raise
    except Exception as exc:
        raise TagWriteError(path, exc) from exc


def write_art(path: Any, data: bytes, mime: str) -> None:
    """Embeds `data` as the file's front-cover art, replacing any
    existing embedded picture(s). Separate from `write_fields` since art
    is a distinct binary pseudo-field (docs/product-spec.md diff model), not
    a tag frame keyed through tags/mapping.py.
    """
    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:
        raise TagWriteError(path, exc) from exc
    if audio is None:
        raise TagWriteError(path, ValueError("unrecognized or unreadable format"))

    try:
        if isinstance(audio.tags, ID3):
            _write_id3_art(audio, data, mime)
        elif isinstance(audio, MP4):
            _write_mp4_art(audio, data, mime)
        elif isinstance(audio, FLAC):
            _write_flac_art(audio, data, mime)
        elif isinstance(audio, OggVorbis | OggOpus):
            _write_ogg_art(audio, data, mime)
        else:
            raise TagWriteError(path, ValueError(f"unsupported format: {type(audio).__name__}"))
        audio.save()
    except TagWriteError:
        raise
    except Exception as exc:
        raise TagWriteError(path, exc) from exc


def clear_art(path: Any) -> None:
    """Removes all embedded picture(s) from the file, if any."""
    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:
        raise TagWriteError(path, exc) from exc
    if audio is None:
        raise TagWriteError(path, ValueError("unrecognized or unreadable format"))

    try:
        if isinstance(audio.tags, ID3):
            audio.tags.delall("APIC")  # type: ignore[no-untyped-call]
        elif isinstance(audio, MP4):
            mp4_tags: Any = audio.tags
            if mp4_tags is not None:
                mp4_tags.pop("covr", None)
        elif isinstance(audio, FLAC):
            audio.clear_pictures()  # type: ignore[no-untyped-call]
        elif isinstance(audio, OggVorbis | OggOpus):
            ogg_tags: Any = audio.tags
            if ogg_tags is not None:
                _vc_del(ogg_tags, "metadata_block_picture")
        else:
            raise TagWriteError(path, ValueError(f"unsupported format: {type(audio).__name__}"))
        audio.save()
    except TagWriteError:
        raise
    except Exception as exc:
        raise TagWriteError(path, exc) from exc


def write_lyrics(path: Any, text: str) -> None:
    """Writes unsynced plain-text lyrics, replacing any existing value.
    Separate from write_fields since lyrics is a large free-text
    pseudo-field with no domain.fields entry (mirrors why write_art is
    separate) and, for ID3, needs USLT's multi-part (lang/desc/text)
    frame construction rather than a flat key mapping."""
    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:
        raise TagWriteError(path, exc) from exc
    if audio is None:
        raise TagWriteError(path, ValueError("unrecognized or unreadable format"))

    try:
        if isinstance(audio.tags, ID3):
            tags: Any = audio.tags
            tags.delall("USLT")
            tags.add(USLT(encoding=Encoding.UTF8, lang="eng", desc="", text=text))  # type: ignore[no-untyped-call]
        elif isinstance(audio, MP4):
            if audio.tags is None:
                audio.add_tags()  # type: ignore[no-untyped-call]
            mp4_tags: Any = audio.tags
            mp4_tags[MP4_LYRICS_KEY] = text
        elif isinstance(audio, FLAC | OggVorbis | OggOpus):
            if audio.tags is None:
                audio.add_tags()  # type: ignore[no-untyped-call]
            ogg_tags: Any = audio.tags
            ogg_tags[VORBIS_LYRICS_KEY] = [text]
        else:
            raise TagWriteError(path, ValueError(f"unsupported format: {type(audio).__name__}"))
        audio.save()
    except TagWriteError:
        raise
    except Exception as exc:
        raise TagWriteError(path, exc) from exc


def clear_lyrics(path: Any) -> None:
    """Removes any lyrics frame/atom/comment from the file, if present."""
    try:
        audio = mutagen.File(path, easy=False)
    except Exception as exc:
        raise TagWriteError(path, exc) from exc
    if audio is None:
        raise TagWriteError(path, ValueError("unrecognized or unreadable format"))

    try:
        if isinstance(audio.tags, ID3):
            audio.tags.delall("USLT")  # type: ignore[no-untyped-call]
        elif isinstance(audio, MP4):
            mp4_tags: Any = audio.tags
            if mp4_tags is not None:
                mp4_tags.pop(MP4_LYRICS_KEY, None)
        elif isinstance(audio, FLAC | OggVorbis | OggOpus):
            ogg_tags: Any = audio.tags
            if ogg_tags is not None:
                _vc_del(ogg_tags, VORBIS_LYRICS_KEY)
        else:
            raise TagWriteError(path, ValueError(f"unsupported format: {type(audio).__name__}"))
        audio.save()
    except TagWriteError:
        raise
    except Exception as exc:
        raise TagWriteError(path, exc) from exc


def _make_picture(data: bytes, mime: str) -> Picture:
    picture = Picture()  # type: ignore[no-untyped-call]
    picture.data = data
    picture.type = 3  # "Cover (front)" — id3.PictureType.COVER_FRONT's value
    picture.mime = mime
    return picture


def _write_id3_art(audio: Any, data: bytes, mime: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags: Any = audio.tags
    tags.delall("APIC")
    tags.add(APIC(encoding=Encoding.UTF8, mime=mime, type=3, desc="", data=data))  # type: ignore[no-untyped-call]


def _write_mp4_art(audio: MP4, data: bytes, mime: str) -> None:
    if audio.tags is None:
        audio.add_tags()  # type: ignore[no-untyped-call]
    tags: Any = audio.tags
    image_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
    tags["covr"] = [MP4Cover(data, imageformat=image_format)]  # type: ignore[no-untyped-call]


def _write_flac_art(audio: FLAC, data: bytes, mime: str) -> None:
    audio.clear_pictures()  # type: ignore[no-untyped-call]
    audio.add_picture(_make_picture(data, mime))  # type: ignore[no-untyped-call]


def _write_ogg_art(audio: Any, data: bytes, mime: str) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags: Any = audio.tags
    picture = _make_picture(data, mime)
    encoded = base64.b64encode(picture.write()).decode("ascii")  # type: ignore[no-untyped-call]
    tags["metadata_block_picture"] = [encoded]


# ---------------------------------------------------------------- Vorbis --


def _vc_del(tags: Any, key: str) -> None:
    # mutagen's VComment mapping supports __delitem__ but not dict.pop's
    # two-arg form; guard for the "already absent" case ourselves.
    if key in tags:
        del tags[key]


def _write_vorbis(audio: Any, field_values: dict[str, Any]) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    for field, value in field_values.items():
        if field == "artists":
            key = VORBIS_KEYS["artists"]
            if value:
                tags[key] = [str(v) for v in value]
            else:
                _vc_del(tags, key)
            continue
        if field in ("genre", "mood"):
            key = VORBIS_KEYS[field]
            if value:
                tags[key] = [str(v) for v in value]
            else:
                _vc_del(tags, key)
            continue

        mapped_key = VORBIS_KEYS.get(field)
        if mapped_key is None:
            continue  # not a mapped field for this format (long tail lives in extra_tags)
        if value is None or value == "":
            _vc_del(tags, mapped_key)
        elif isinstance(value, bool):
            tags[mapped_key] = ["1" if value else "0"]
        else:
            tags[mapped_key] = [str(value)]


# ------------------------------------------------------------------ MP4 --


def _write_mp4(audio: MP4, field_values: dict[str, Any]) -> None:
    if audio.tags is None:
        audio.add_tags()  # type: ignore[no-untyped-call]
    tags: Any = audio.tags

    for field, value in field_values.items():
        if field == "track_no" or field == "track_total":
            existing = tags.get("trkn", [(0, 0)])[0]
            no, total = existing
            if field == "track_no":
                no = value or 0
            else:
                total = value or 0
            tags["trkn"] = [(no, total)]
            continue
        if field == "disc_no" or field == "disc_total":
            existing = tags.get("disk", [(0, 0)])[0]
            no, total = existing
            if field == "disc_no":
                no = value or 0
            else:
                total = value or 0
            tags["disk"] = [(no, total)]
            continue
        if field == "compilation":
            tags[MP4_STANDARD_KEYS["compilation"]] = bool(value)
            continue
        if field == "bpm":
            if value is None:
                tags.pop(MP4_STANDARD_KEYS["bpm"], None)
            else:
                tags[MP4_STANDARD_KEYS["bpm"]] = [int(value)]
            continue
        if field == "genre":
            key = MP4_STANDARD_KEYS["genre"]
            if value:
                tags[key] = [str(v) for v in value]
            else:
                tags.pop(key, None)
            continue

        if field in MP4_STANDARD_KEYS:
            key = MP4_STANDARD_KEYS[field]
            if value is None or value == "":
                tags.pop(key, None)
            else:
                tags[key] = [str(value)]
            continue

        if field in MP4_FREEFORM_KEYS:
            name = MP4_FREEFORM_KEYS[field]
            key = f"----:{MP4_FREEFORM_MEAN}:{name}"
            if field == "mood":
                mood_val = value[0] if value else None
                if not mood_val:
                    tags.pop(key, None)
                else:
                    tags[key] = [str(mood_val).encode("utf-8")]
                continue
            if value is None or value == "":
                tags.pop(key, None)
            else:
                tags[key] = [str(value).encode("utf-8")]
            continue
        # unmapped field for MP4 — skip (long tail)


# ------------------------------------------------------------------ ID3 --


def _id3_set_text(tags: Any, frame_id: str, value: str) -> None:
    if frame_id.startswith("TXXX:"):
        desc = frame_id.split(":", 1)[1]
        tags.setall("TXXX:" + desc, [TXXX(encoding=Encoding.UTF8, desc=desc, text=[value])])  # type: ignore[no-untyped-call]
        return
    frame_cls: Any = mutagen.id3.Frames[frame_id]
    tags.setall(frame_id, [frame_cls(encoding=Encoding.UTF8, text=[value])])


def _id3_del(tags: Any, frame_id: str) -> None:
    if frame_id.startswith("TXXX:"):
        desc = frame_id.split(":", 1)[1]
        tags.delall("TXXX:" + desc)
        return
    if frame_id.startswith("UFID:"):
        tags.delall(frame_id)
        return
    if frame_id.startswith("COMM"):
        tags.delall("COMM")
        return
    tags.delall(frame_id)


def _write_id3(audio: Any, field_values: dict[str, Any]) -> None:
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    for field, value in field_values.items():
        if field == "track_no" or field == "track_total":
            no, total = _split_current(tags, "TRCK")
            if field == "track_no":
                no = str(value) if value is not None else None
            else:
                total = str(value) if value is not None else None
            _write_num_total(tags, TRCK, no, total)
            continue
        if field == "disc_no" or field == "disc_total":
            no, total = _split_current(tags, "TPOS")
            if field == "disc_no":
                no = str(value) if value is not None else None
            else:
                total = str(value) if value is not None else None
            _write_num_total(tags, TPOS, no, total)
            continue
        if field == "mb_recording_id":
            if value:
                tags.setall(
                    "UFID:http://musicbrainz.org",
                    [UFID(owner="http://musicbrainz.org", data=str(value).encode("ascii"))],  # type: ignore[no-untyped-call]
                )
            else:
                tags.delall("UFID:http://musicbrainz.org")
            continue
        if field == "comment":
            if value:
                tags.setall(
                    "COMM",
                    [COMM(encoding=Encoding.UTF8, lang="eng", desc="", text=[str(value)])],  # type: ignore[no-untyped-call]
                )
            else:
                tags.delall("COMM")
            continue
        if field == "genre":
            if value:
                tags.setall("TCON", [TCON(encoding=Encoding.UTF8, text=[str(v) for v in value])])  # type: ignore[no-untyped-call]
            else:
                tags.delall("TCON")
            continue
        if field == "mood":
            if value:
                tags.setall("TMOO", [TMOO(encoding=Encoding.UTF8, text=[str(v) for v in value])])  # type: ignore[no-untyped-call]
            else:
                tags.delall("TMOO")
            continue
        if field == "compilation":
            tags.setall("TCMP", [TCMP(encoding=Encoding.UTF8, text=["1" if value else "0"])])  # type: ignore[no-untyped-call]
            continue
        if field == "date":
            if value:
                tags.setall("TDRC", [TDRC(encoding=Encoding.UTF8, text=[str(value)])])  # type: ignore[no-untyped-call]
            else:
                tags.delall("TDRC")
            continue
        if field == "artists":
            continue  # ID3 has no dedicated multi-artist frame in this mapping

        frame_id = ID3_FRAMES.get(field)
        if frame_id is None:
            continue  # unmapped for ID3 — long tail
        if value is None or value == "":
            _id3_del(tags, frame_id)
        else:
            _id3_set_text(tags, frame_id, str(value))


def _split_current(tags: Any, frame_id: str) -> tuple[str | None, str | None]:
    frame = tags.get(frame_id)
    if frame is None or not frame.text:
        return None, None
    raw = str(frame.text[0])
    parts = raw.split("/", 1)
    no = parts[0] if parts[0] else None
    total = parts[1] if len(parts) > 1 and parts[1] else None
    return no, total


def _write_num_total(tags: Any, frame_cls: Any, no: str | None, total: str | None) -> None:
    frame_id = frame_cls.__name__
    if no is None and total is None:
        tags.delall(frame_id)
        return
    text = no or "0"
    if total:
        text = f"{text}/{total}"
    tags.setall(frame_id, [frame_cls(encoding=Encoding.UTF8, text=[text])])
