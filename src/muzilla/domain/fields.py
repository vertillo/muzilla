"""The canonical metadata field registry.

Every other subsystem derives from this file: tag-format mapping, diff
labels, path template variables, API schemas, and UI forms. Adding a
field to muzilla means editing this file and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FieldType(Enum):
    TEXT = "text"
    INT = "int"
    FLOAT = "float"
    DATE = "date"
    BOOL = "bool"
    MULTI_TEXT = "multi_text"  # ordered list of strings (artists, genres)


class FieldCategory(Enum):
    IDENTITY = "identity"  # title, artist, album...
    RELEASE = "release"  # label, catalog_number, barcode, country, media
    CLASSIFICATION = "classification"  # genre, mood, bpm, key
    PROVIDER_ID = "provider_id"  # mb_track_id, discogs_release_id...
    AUDIO_ANALYSIS = "audio_analysis"  # replaygain, fingerprint
    TECHNICAL = "technical"  # duration, bitrate, codec (read-only, from probe)
    ADMIN = "admin"  # comment, encoder — common strip targets


@dataclass(frozen=True)
class FieldDef:
    name: str
    label: str
    type: FieldType
    category: FieldCategory
    participates_in_matching: bool = False
    match_weight: float = 0.0
    editable: bool = True
    default_strip: bool = False
    """Whether this field is stripped by default in a fresh strip-rules config."""
    description: str = ""


FIELDS: dict[str, FieldDef] = {}


def _register(*defs: FieldDef) -> None:
    for d in defs:
        if d.name in FIELDS:
            raise ValueError(f"duplicate field registration: {d.name}")
        FIELDS[d.name] = d


_register(
    # --- Identity ---
    FieldDef("title", "Title", FieldType.TEXT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=3.0),
    FieldDef("artist", "Artist", FieldType.TEXT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=2.0),
    FieldDef("artists", "Artists", FieldType.MULTI_TEXT, FieldCategory.IDENTITY),
    FieldDef("album", "Album", FieldType.TEXT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=3.0),
    FieldDef("album_artist", "Album Artist", FieldType.TEXT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=3.0),
    FieldDef("composer", "Composer", FieldType.TEXT, FieldCategory.IDENTITY),
    FieldDef("track_no", "Track Number", FieldType.INT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=1.0),
    FieldDef("track_total", "Track Total", FieldType.INT, FieldCategory.IDENTITY),
    FieldDef("disc_no", "Disc Number", FieldType.INT, FieldCategory.IDENTITY),
    FieldDef("disc_total", "Disc Total", FieldType.INT, FieldCategory.IDENTITY),
    FieldDef("year", "Year", FieldType.INT, FieldCategory.IDENTITY,
             participates_in_matching=True, match_weight=0.5),
    FieldDef("original_year", "Original Year", FieldType.INT, FieldCategory.IDENTITY),
    FieldDef("date", "Date", FieldType.DATE, FieldCategory.IDENTITY),
    FieldDef("compilation", "Compilation", FieldType.BOOL, FieldCategory.IDENTITY),

    # --- Release ---
    FieldDef("label", "Label", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=0.5),
    FieldDef("catalog_number", "Catalog Number", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=0.5),
    FieldDef("barcode", "Barcode", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=2.0),
    FieldDef("isrc", "ISRC", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=4.0),
    FieldDef("country", "Country", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=0.5),
    FieldDef("media", "Media", FieldType.TEXT, FieldCategory.RELEASE,
             participates_in_matching=True, match_weight=0.5),

    # --- Classification ---
    FieldDef("genre", "Genre", FieldType.MULTI_TEXT, FieldCategory.CLASSIFICATION),
    FieldDef("mood", "Mood", FieldType.MULTI_TEXT, FieldCategory.CLASSIFICATION),
    FieldDef("bpm", "BPM", FieldType.INT, FieldCategory.CLASSIFICATION),
    FieldDef("key", "Key", FieldType.TEXT, FieldCategory.CLASSIFICATION),

    # --- Provider IDs ---
    FieldDef("mb_track_id", "MusicBrainz Track ID", FieldType.TEXT, FieldCategory.PROVIDER_ID,
             participates_in_matching=True, match_weight=5.0),
    FieldDef("mb_release_id", "MusicBrainz Release ID", FieldType.TEXT, FieldCategory.PROVIDER_ID,
             participates_in_matching=True, match_weight=5.0),
    FieldDef("mb_recording_id", "MusicBrainz Recording ID", FieldType.TEXT, FieldCategory.PROVIDER_ID),
    FieldDef("mb_artist_id", "MusicBrainz Artist ID", FieldType.TEXT, FieldCategory.PROVIDER_ID),
    FieldDef("discogs_release_id", "Discogs Release ID", FieldType.TEXT, FieldCategory.PROVIDER_ID),
    FieldDef("deezer_track_id", "Deezer Track ID", FieldType.TEXT, FieldCategory.PROVIDER_ID),
    FieldDef("acoustid_id", "AcoustID", FieldType.TEXT, FieldCategory.PROVIDER_ID),

    # --- Audio analysis ---
    FieldDef("acoustid_fingerprint", "Fingerprint", FieldType.TEXT, FieldCategory.AUDIO_ANALYSIS,
             editable=False),
    FieldDef("rg_track_gain", "ReplayGain Track Gain", FieldType.FLOAT, FieldCategory.AUDIO_ANALYSIS),
    FieldDef("rg_track_peak", "ReplayGain Track Peak", FieldType.FLOAT, FieldCategory.AUDIO_ANALYSIS),
    FieldDef("rg_album_gain", "ReplayGain Album Gain", FieldType.FLOAT, FieldCategory.AUDIO_ANALYSIS),
    FieldDef("rg_album_peak", "ReplayGain Album Peak", FieldType.FLOAT, FieldCategory.AUDIO_ANALYSIS),
    FieldDef("r128_track_gain", "R128 Track Gain", FieldType.FLOAT, FieldCategory.AUDIO_ANALYSIS),

    # --- Technical (read-only, from probe) ---
    FieldDef("duration_ms", "Duration", FieldType.INT, FieldCategory.TECHNICAL,
             participates_in_matching=True, match_weight=2.0, editable=False),
    FieldDef("bitrate", "Bitrate", FieldType.INT, FieldCategory.TECHNICAL, editable=False),
    FieldDef("sample_rate", "Sample Rate", FieldType.INT, FieldCategory.TECHNICAL, editable=False),
    FieldDef("channels", "Channels", FieldType.INT, FieldCategory.TECHNICAL, editable=False),
    FieldDef("codec", "Codec", FieldType.TEXT, FieldCategory.TECHNICAL, editable=False),

    # --- Admin / common strip targets ---
    FieldDef("comment", "Comment", FieldType.TEXT, FieldCategory.ADMIN, default_strip=True),
    FieldDef("encoder", "Encoder", FieldType.TEXT, FieldCategory.ADMIN, editable=False,
             default_strip=True),
)


def get(name: str) -> FieldDef:
    return FIELDS[name]


def matching_fields(category: FieldCategory | None = None) -> list[FieldDef]:
    return [
        f for f in FIELDS.values()
        if f.participates_in_matching and (category is None or f.category == category)
    ]


def strippable_fields() -> list[FieldDef]:
    return [f for f in FIELDS.values() if f.editable]


def default_strip_fields() -> list[FieldDef]:
    return [f for f in FIELDS.values() if f.default_strip]
