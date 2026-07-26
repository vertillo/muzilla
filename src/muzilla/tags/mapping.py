"""Per-format tag-frame mapping tables.

Each supported container format has a different native tag scheme
(ID3v2 frames, Vorbis comments, MP4 atoms, ...). This module is the
single place that knows how a canonical field name maps to each
format's native key — reader.py and writer.py both consult it so the
mapping never drifts between read and write paths.

ID3 (MP3, WAV/RIFF INFO+ID3, AIFF ID3) uses frame IDs; the value
extraction differs per frame kind (TXXX free text, TXXX:isrc user
frames, COMM comments, POPM, etc.) so id3 mapping is handled with
explicit logic in reader.py/writer.py rather than a flat dict —
frame construction isn't a 1:1 string mapping. VORBIS_KEYS and MP4_KEYS
below ARE flat 1:1 mappings, since Vorbis comments and MP4 atoms are
simple key/value(-list) stores.
"""

from __future__ import annotations

# Vorbis comment field names (FLAC, Ogg Vorbis, Ogg Opus) are free-text,
# conventionally uppercase, and multi-valued by nature (repeated keys).
# https://www.xiph.org/vorbis/doc/v-comment.html + common extensions.
VORBIS_KEYS: dict[str, str] = {
    "title": "TITLE",
    "artist": "ARTIST",
    "artists": "ARTIST",  # multi-valued via repeated ARTIST keys
    "album": "ALBUM",
    "album_artist": "ALBUMARTIST",
    "composer": "COMPOSER",
    "track_no": "TRACKNUMBER",
    "track_total": "TRACKTOTAL",
    "disc_no": "DISCNUMBER",
    "disc_total": "DISCTOTAL",
    "date": "DATE",
    "original_year": "ORIGINALYEAR",
    "compilation": "COMPILATION",
    "label": "LABEL",
    "catalog_number": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "isrc": "ISRC",
    "country": "RELEASECOUNTRY",
    "media": "MEDIA",
    "genre": "GENRE",
    "mood": "MOOD",
    "bpm": "BPM",
    "key": "INITIALKEY",
    "mb_track_id": "MUSICBRAINZ_RELEASETRACKID",
    "mb_release_id": "MUSICBRAINZ_ALBUMID",
    "mb_recording_id": "MUSICBRAINZ_TRACKID",
    "mb_artist_id": "MUSICBRAINZ_ARTISTID",
    "discogs_release_id": "DISCOGS_RELEASE_ID",
    "deezer_track_id": "DEEZER_TRACK_ID",
    "acoustid_id": "ACOUSTID_ID",
    "acoustid_fingerprint": "ACOUSTID_FINGERPRINT",
    "rg_track_gain": "REPLAYGAIN_TRACK_GAIN",
    "rg_track_peak": "REPLAYGAIN_TRACK_PEAK",
    "rg_album_gain": "REPLAYGAIN_ALBUM_GAIN",
    "rg_album_peak": "REPLAYGAIN_ALBUM_PEAK",
    "comment": "COMMENT",
    "encoder": "ENCODER",
}

# MP4/M4A atom names. '----:com.apple.iTunes:X' freeform atoms are used
# for fields with no standard atom (ISRC, barcode, MusicBrainz IDs, etc).
MP4_STANDARD_KEYS: dict[str, str] = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album_artist": "aART",
    "album": "\xa9alb",
    "composer": "\xa9wrt",
    "date": "\xa9day",
    "genre": "\xa9gen",
    "comment": "\xa9cmt",
    "encoder": "\xa9too",
    "compilation": "cpil",
    "bpm": "tmpo",
    # track_no/disc_no are tuples: ('trkn', [(no, total)])
    "track_no": "trkn",
    "disc_no": "disk",
}

MP4_FREEFORM_MEAN = "com.apple.iTunes"
MP4_FREEFORM_KEYS: dict[str, str] = {
    "label": "LABEL",
    "catalog_number": "CATALOGNUMBER",
    "barcode": "BARCODE",
    "isrc": "ISRC",
    "country": "MusicBrainz Album Release Country",
    "media": "MEDIA",
    "mood": "MOOD",
    "key": "initialkey",
    "mb_track_id": "MusicBrainz Release Track Id",
    "mb_release_id": "MusicBrainz Album Id",
    "mb_recording_id": "MusicBrainz Track Id",
    "mb_artist_id": "MusicBrainz Artist Id",
    "discogs_release_id": "DISCOGS_RELEASE_ID",
    "deezer_track_id": "DEEZER_TRACK_ID",
    "acoustid_id": "Acoustid Id",
    "acoustid_fingerprint": "Acoustid Fingerprint",
    "rg_track_gain": "replaygain_track_gain",
    "rg_track_peak": "replaygain_track_peak",
    "rg_album_gain": "replaygain_album_gain",
    "rg_album_peak": "replaygain_album_peak",
}

# ID3v2 (MP3, and RIFF/AIFF ID3 chunks) frame IDs. TXXX: entries are
# user-defined text frames addressed as f"TXXX:{description}".
ID3_FRAMES: dict[str, str] = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "album_artist": "TPE2",
    "composer": "TCOM",
    "track_no": "TRCK",  # "n/total"
    "disc_no": "TPOS",  # "n/total"
    "date": "TDRC",
    "genre": "TCON",
    "bpm": "TBPM",
    "isrc": "TSRC",
    "label": "TPUB",
    "media": "TMED",
    "encoder": "TENC",
    "mb_track_id": "TXXX:MusicBrainz Release Track Id",
    "mb_release_id": "TXXX:MusicBrainz Album Id",
    "mb_recording_id": "UFID:http://musicbrainz.org",
    "mb_artist_id": "TXXX:MusicBrainz Artist Id",
    "discogs_release_id": "TXXX:DISCOGS_RELEASE_ID",
    "deezer_track_id": "TXXX:DEEZER_TRACK_ID",
    "acoustid_id": "TXXX:Acoustid Id",
    "acoustid_fingerprint": "TXXX:Acoustid Fingerprint",
    "catalog_number": "TXXX:CATALOGNUMBER",
    "barcode": "TXXX:BARCODE",
    "country": "TXXX:MusicBrainz Album Release Country",
    "original_year": "TXXX:originalyear",
    "compilation": "TCMP",
    "mood": "TMOO",
    "key": "TKEY",
    "rg_track_gain": "TXXX:replaygain_track_gain",
    "rg_track_peak": "TXXX:replaygain_track_peak",
    "rg_album_gain": "TXXX:replaygain_album_gain",
    "rg_album_peak": "TXXX:replaygain_album_peak",
    "comment": "COMM::eng",
}
